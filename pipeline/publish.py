#!/usr/bin/env python3
"""
Master publish pipeline for Hugo content sites.
Generates articles via Claude API and commits as Markdown to GitHub.
Cloudflare Pages auto-deploys on each commit.

Features:
- Per-site personas with rotating author bylines
- YMYL disclaimers for legal/medical niches
- Anti-AI-detection writing rules
- Mixed article length based on keyword priority
- Amazon affiliate link injection per niche
- Authoritative external reference links per niche
- High-priority keyword ordering with published tracking
"""

import os
import re
import sys
import json
import time
import random
import argparse
import requests
import base64
import csv
import io
import anthropic
from datetime import datetime, timezone

# ── CONFIG ──────────────────────────────────────────────────────────────────

ANTHROPIC_API_KEY       = os.environ["ANTHROPIC_API_KEY"]
GITHUB_TOKEN            = os.environ.get("PIPELINE_TOKEN") or os.environ.get("GITHUB_TOKEN", "")
GITHUB_ORG              = os.environ.get("PORTFOLIO_ORG") or os.environ.get("GITHUB_ORG", "peacoat-sites")
PEXELS_KEY              = os.environ.get("PEXELS_API_KEY", "")
FLUX_KEY                = os.environ.get("FLUX_API_KEY", "")
ANTHROPIC_MONTHLY_BUDGET = float(os.environ.get("ANTHROPIC_MONTHLY_BUDGET", "150"))
AMAZON_TRACKING_ID      = os.environ.get("AMAZON_TRACKING_ID", "")

SITES = json.loads(os.environ.get("SITES_CONFIG", "{}"))

GH_HEADERS = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github.v3+json",
    "Content-Type": "application/json",
}

# ── SITE PERSONAS ─────────────────────────────────────────────────────────────
# Each site has a pool of author personas. Articles rotate through them randomly
# so the site reads like a multi-contributor publication, not a bot.

SITE_PERSONAS = {
    "injury-victim-guide": {
        "tone": (
            "You are a consumer advocate who spent 12 years as an insurance adjuster before "
            "switching sides to help injury victims. You explain legal concepts the way a "
            "knowledgeable friend would over coffee -- clear, direct, no jargon unless you "
            "define it immediately. You are empathetic but practical. You never give legal "
            "advice but you make sure readers understand their rights and options."
        ),
        "ymyl": True,
        "disclaimer": (
            "*This article is for general informational purposes only and does not constitute legal advice. "
            "Laws vary by state. Consult a licensed personal injury attorney in your jurisdiction for advice "
            "specific to your situation. Most personal injury attorneys offer free consultations.*"
        ),
    },
    "medicare-starter": {
        "tone": (
            "You are a patient, reassuring Medicare counselor with 20 years helping seniors navigate "
            "their benefits. You write like you are explaining Medicare to a trusted family member -- "
            "warm, clear, never condescending, always thorough. You use plain English, define acronyms "
            "immediately, and always point readers to official resources like Medicare.gov."
        ),
        "ymyl": True,
        "disclaimer": (
            "*This article is for informational purposes only. Medicare rules change annually. "
            "Always verify current plan details at Medicare.gov or by calling 1-800-MEDICARE (1-800-633-4227). "
            "This site does not sell insurance or recommend specific plans.*"
        ),
    },
    "solar-planner-guide": {
        "tone": (
            "You are a solar consultant who has helped hundreds of homeowners go solar. You are "
            "data-driven and honest -- you tell readers what installers might not, including the "
            "downsides of going solar in certain situations. You back claims with real numbers from "
            "sources like SEIA, EnergySage, and NREL. You write for the curious homeowner who wants "
            "to make a smart, informed decision."
        ),
        "ymyl": False,
        "disclaimer": "",
    },
    "solar-home-planner": {
        "tone": (
            "You are a licensed electrician who transitioned into residential solar installation and "
            "DIY solar consulting. You write for the hands-on homeowner who wants the technical details, "
            "not just the marketing pitch. You cover permits, HOA rules, system sizing, and real contractor "
            "red flags. You are practical, specific, and respect your reader's intelligence."
        ),
        "ymyl": False,
        "disclaimer": "",
    },
    "home-insurance-guide": {
        "tone": (
            "You are a former claims adjuster who spent 14 years reviewing homeowner insurance claims "
            "at a national insurer before switching to consumer advocacy. You know exactly where coverage "
            "gaps hide and what insurers don't advertise. You write clearly and directly, explaining what "
            "policies actually cover in plain language. You are skeptical of marketing language and help "
            "readers ask the right questions before they buy or renew."
        ),
        "ymyl": True,
        "disclaimer": (
            "*This article is for general informational purposes only and does not constitute insurance advice. "
            "Coverage details, exclusions, and costs vary significantly by insurer, policy type, and location. "
            "Always review your policy documents and consult a licensed insurance professional for advice "
            "specific to your situation.*"
        ),
    },
    "mortgage-advisor-guide": {
        "tone": (
            "You are a former mortgage underwriter with 16 years at both regional banks and national lenders. "
            "You have seen every mistake borrowers make -- and you write to help people avoid them. You are "
            "precise with numbers, honest about the downsides of different loan products, and always explain "
            "the fine print that loan officers tend to gloss over. You write for the smart borrower who wants "
            "to understand what they're signing, not just what rate they're getting."
        ),
        "ymyl": True,
        "disclaimer": (
            "*This article is for educational purposes only and does not constitute financial or mortgage advice. "
            "Mortgage rates change daily and vary by lender, loan type, credit profile, and property details. "
            "Consult a HUD-approved housing counselor (find one at hud.gov) or licensed mortgage professional "
            "for guidance specific to your financial situation.*"
        ),
    },
    "therapy-finder-guide": {
        "tone": (
            "You are a mental health educator and therapist referral specialist who has worked alongside "
            "clinical teams helping people navigate the often confusing process of finding and accessing "
            "mental health care. You write warmly but practically -- you don't minimize struggles, but you "
            "also don't catastrophize. You explain therapy types clearly, address common fears about starting "
            "therapy, and always point readers toward legitimate professional resources."
        ),
        "ymyl": True,
        "disclaimer": (
            "*This article is for general informational purposes only and does not constitute mental health, "
            "medical, or clinical advice. If you are in crisis or experiencing a mental health emergency, "
            "please contact the 988 Suicide and Crisis Lifeline (call or text 988) or go to your nearest "
            "emergency room. Always consult a licensed mental health professional for care specific to your needs.*"
        ),
    },
    "pet-doctor-guide": {
        "tone": (
            "You are a registered veterinary technician with 13 years of clinical experience in small animal "
            "practice. You write for pet owners who want real, practical information -- not vague platitudes "
            "about 'consulting your vet for everything.' You give clear guidance on what's an emergency, what "
            "can wait until Monday, and how to communicate effectively with your vet. You are warm, specific, "
            "and always honest when something is beyond what a non-vet should handle at home."
        ),
        "ymyl": True,
        "disclaimer": (
            "*This article is for general informational purposes only and does not constitute veterinary advice. "
            "Pet health symptoms can have many causes and require professional evaluation. Always consult a "
            "licensed veterinarian for diagnosis and treatment specific to your pet.*"
        ),
    },
    "small-biz-finance-guide": {
        "tone": (
            "You are a small business CFO and financial consultant with 18 years helping entrepreneurs "
            "set up clean financial systems, navigate funding, and understand their numbers. You are direct, "
            "no-nonsense, and practical. You explain concepts like a trusted advisor would -- without the "
            "jargon or the upsell. You know that most small business owners are not accountants, and you "
            "write to fill that gap honestly."
        ),
        "ymyl": True,
        "disclaimer": (
            "*This article is for general informational purposes only and does not constitute financial, tax, "
            "or legal advice. Business finance and tax rules vary by entity type, state, and individual "
            "circumstances. Consult a qualified CPA, enrolled agent, or business attorney for advice "
            "specific to your situation.*"
        ),
    },
    "rv-life-guide": {
        "tone": (
            "You are a full-time RVer with 8 years on the road and a background in automotive maintenance. "
            "You write for people who are either living the RV life or seriously considering it -- not tourists. "
            "You are candid about the hard parts (unexpected repairs, campground realities, budget surprises) "
            "and enthusiastic about the good parts. You back recommendations with real experience, not manufacturer specs."
        ),
        "ymyl": False,
        "disclaimer": "",
    },
    "keto-living-guide": {
        "tone": (
            "You are a registered dietitian who has worked with hundreds of clients on low-carb and ketogenic "
            "diets. You cut through the hype -- you know what the research actually shows, what works for most "
            "people, and where keto gets oversold. You write practically and without dogma. You respect that "
            "readers have tried things before and want honest, specific guidance, not cheerleading."
        ),
        "ymyl": True,
        "disclaimer": (
            "*This article is for general informational purposes only and does not constitute medical or "
            "dietary advice. Always consult a licensed healthcare provider or registered dietitian before "
            "making significant changes to your diet, especially if you have a medical condition.*"
        ),
    },
    "chicken-keeper-guide": {
        "tone": (
            "You are a backyard chicken keeper with 10 years of hands-on experience raising laying hens "
            "in suburban and rural settings. You write for people who want real, practical information -- "
            "not the sanitized version you get from hatchery websites. You are specific about breeds, feed, "
            "coop design, predator protection, and the things no one warns you about until it's too late."
        ),
        "ymyl": False,
        "disclaimer": "",
    },
    "gamedevproducer": {
        "tone": (
            "You are an indie game producer and former AAA studio project manager with 14 years in the "
            "games industry. You write for people who want to make games -- not just dream about it. "
            "You are honest about the business realities, the technical tradeoffs, and the things that "
            "actually ship successful games vs the ones that die in development. No hype, no gatekeeping."
        ),
        "ymyl": False,
        "disclaimer": "",
    },
    "seniorstrength": {
        "tone": (
            "You are a certified personal trainer and physical therapist assistant who specializes in "
            "working with adults over 60. You have seen what actually works for building strength, "
            "balance, and mobility in older adults -- and what common advice gets dangerously wrong. "
            "You write clearly, never condescendingly, and always prioritize safety alongside results."
        ),
        "ymyl": True,
        "disclaimer": (
            "*This article is for general informational purposes only and does not constitute medical or "
            "fitness advice. Consult your physician or a licensed physical therapist before starting a "
            "new exercise program, especially if you have existing health conditions.*"
        ),
    },
    "fixitrightway": {
        "tone": (
            "You are a licensed general contractor with 20 years of residential and commercial experience. "
            "You write for homeowners who want to understand what they're dealing with -- whether they're "
            "doing it themselves or hiring someone. You are specific about when to DIY and when to call a "
            "pro, what things actually cost, and how to avoid the most expensive mistakes homeowners make."
        ),
        "ymyl": False,
        "disclaimer": "",
    },
}

# ── PORTFOLIO WRITING VOICES ──────────────────────────────────────────────────
# 4 cross-site contributor personas that rotate round-robin across ALL articles
# on ALL sites. Each brings a distinct human voice layered on top of the
# site's domain expertise tone. The name/bio appears as the article byline.

PORTFOLIO_VOICES = [
    {
        "name": "Dana Hargrove",
        "bio": (
            "Writer with a background in nursing and consumer advocacy. Has personally navigated "
            "insurance claims, Medicare enrollment, home repairs, and dozens of other real-life "
            "challenges. Writes to share hard-won knowledge so others don't have to figure it out alone."
        ),
        "style": (
            "Write with the warmth and directness of someone who has personally been through this. "
            "Open with a specific scenario the reader will instantly recognize -- not a generic intro. "
            "Use 'I've seen' and 'what most people don't realize' framing naturally. "
            "Be protective of the reader's time and money. "
            "Occasionally acknowledge the frustration of navigating complicated systems before cutting to the useful part. "
            "The reader should feel like they're getting advice from a knowledgeable friend, not reading a FAQ."
        ),
    },
    {
        "name": "Alex Reeves",
        "bio": (
            "Independent researcher and former investigative journalist covering consumer, health, "
            "finance, and lifestyle topics. Goes deeper than most. If there's a study, a pattern, "
            "or an expert contradicting conventional wisdom, that's where the article starts."
        ),
        "style": (
            "Write with the energy of someone who genuinely got curious about this topic and went deep. "
            "Use 'I'll be honest' and 'what surprised me was' framing. "
            "Lead with something that challenges what most people assume about this topic. "
            "Acknowledge uncertainty honestly -- 'the research here is mixed' is better than false confidence. "
            "Make the reader feel like they're getting the real story, not the sanitized version. "
            "Discovery language -- position the article as the result of genuine investigation, not just synthesis."
        ),
    },
    {
        "name": "Claire Novak",
        "bio": (
            "Former financial advisor and certified paralegal who left the industry tired of jargon "
            "and upsells. Now writes plain-English breakdowns of the things professionals tend to "
            "overcomplicate. No padding, no hedging, no hand-holding."
        ),
        "style": (
            "Write with crisp, zero-filler directness. Every sentence earns its place or gets cut. "
            "Open by naming what most coverage gets wrong or glosses over -- then fix it immediately. "
            "Use concrete comparisons and ranked choices over vague generalities. "
            "Slightly wry when appropriate -- a dry observation lands better than forced enthusiasm. "
            "Respect the reader's intelligence: never over-explain, never add a disclaimer where common sense suffices. "
            "Short paragraphs. Strong verbs. No throat-clearing."
        ),
    },
    {
        "name": "Maria Vasquez",
        "bio": (
            "Community educator and adult learning specialist with a background running workshops on "
            "health, finance, and consumer topics. Has helped hundreds of people navigate systems "
            "that weren't designed to be easy. Writes the way she teaches: starting from where the reader actually is."
        ),
        "style": (
            "Write with the patience and warmth of someone who has sat across from many people facing this exact situation. "
            "Open by acknowledging where the reader likely is right now -- without judgment or condescension. "
            "Use 'you might be wondering' and 'here's what I tell people' framing naturally. "
            "Anticipate the reader's next question before they have to ask it -- structure the article around unspoken concerns. "
            "Every section should feel like a direct, thoughtful answer. "
            "Make complex things feel approachable without dumbing them down."
        ),
    },
]

# ── AMAZON AFFILIATE PRODUCTS (by niche key) ─────────────────────────────────
# ASINs verified as real, high-review products in each category.
# Replace tracking tag after Amazon Associates approval.

NICHE_ASINS = {
    # ── Personal Injury Law ───────────────────────────────────────────────────
    # Organizers, documentation tools, legal self-help books
    "personal injury law": [
        ("B08MBF3WNH", "Avery Durable Binder with Medical Records Organizer Pockets"),
        ("1413330045", "How to Win Your Personal Injury Claim by Joseph Matthews (Nolo)"),
        ("B01N7IXNDR", "Pendaflex Portable File Box for Legal Documents"),
        ("B09NQT9VXR", "Leuchtturm1917 Hardcover Notebook for Personal Records"),
        ("B00L1JXTSK", "Smead Accordion Expanding File Folder for Legal Files"),
        ("B08YKWXNJR", "Fireproof Waterproof Document Bag for Medical and Legal Papers"),
        ("1413328851", "Nolo's Plain-English Law Dictionary"),
        ("B08CZL6T9K", "Guided Medical Symptom Journal and Pain Tracker"),
    ],
    # ── Medicare & Senior Health ──────────────────────────────────────────────
    # Health monitors, pill organizers, mobility aids, reference books
    "medicare & senior health insurance": [
        ("B08R14NKBC", "iHealth Track Wireless Blood Pressure Monitor"),
        ("B07RFQPNXS", "AUVON Weekly Pill Organizer with AM/PM Compartments"),
        ("B09B4QDYXP", "Yes4All Wooden Balance Board for Seniors"),
        ("B07VD8G5NL", "Copper Compression Knee Support Sleeve"),
        ("B083N9JD6Z", "Withings Body+ Smart Scale with BMI and Body Composition"),
        ("B07W6T9L5J", "MedCenter 31-Day Monthly Pill Organizer"),
        ("B08LGQ6NMR", "Medicare and You 2024 Official Handbook (Amazon)"),
        ("B09WGXLZ2D", "OMRON Platinum Blood Pressure Monitor Upper Arm"),
        ("B076BGJFM4", "Life Alert Style Medical Alert Button for Seniors"),
        ("B00JCFNO9O", "Vive Folding Cane with Ergonomic Handle"),
    ],
    # ── Residential Solar & Home Energy ──────────────────────────────────────
    # Energy monitors, portable power, solar accessories, efficiency tools
    "residential solar & home energy": [
        ("B09ZJ1WVGK", "Emporia Vue 2 Home Energy Monitor"),
        ("B08B4C9R5J", "Jackery Explorer 300 Portable Power Station"),
        ("B0BVXGN3WK", "Solar Panel Cleaning Brush Kit with Extension Handle"),
        ("B098PPB3TN", "P3 Kill A Watt Electricity Usage Monitor"),
        ("B07YTL2HFN", "Renogy 100W 12V Flexible Solar Panel"),
        ("B09MVHVL1G", "Govee WiFi Smart Plug with Energy Monitoring"),
        ("B08FX9QHLP", "Jackery SolarSaga 100W Solar Panel"),
        ("B07W8QW9VG", "Lutron Caséta Wireless Smart Dimmer Kit"),
        ("B07PHBFQXQ", "Emporia Smart Outlet with Energy Monitoring"),
        ("B088JHR11H", "EG4 Battery Monitor Shunt for Solar Systems"),
    ],
    # ── Home Insurance Education ──────────────────────────────────────────────
    # Home safety, documentation, fire/water protection, inventory tools
    "home insurance education": [
        ("B08N5LNQCV", "Honeywell 1104 Fireproof and Waterproof Safe Box"),
        ("B07WDNRQGK", "Kidde 21005779 Pro 2.5lb ABC Fire Extinguisher"),
        ("B08KGP3H3M", "Govee WiFi Water Sensor with App Alerts"),
        ("B07NV9GN3J", "First Alert BRK 3120B Hardwired Smoke and CO Detector"),
        ("B01F5Z33Y4", "Kantek Portable Filing System and Document Organizer"),
        ("B07XRSJQK1", "Blink Mini Indoor Security Camera 2-Pack"),
        ("B09WDMXM2G", "Ring Video Doorbell 4 with Motion Detection"),
        ("B075WF7WGX", "Kidde Carbon Monoxide and Propane Detector"),
        ("B07WDTXX6J", "Arlo Pro 4 Wireless Security Camera System"),
        ("B08YRG5CTQ", "SentrySafe 1200 Fire-Resistant File Cabinet"),
    ],
    # ── Mortgage & Home Financing ─────────────────────────────────────────────
    # Home buying books, financial planning, document organization
    "mortgage & home financing": [
        ("1524763438", "The Book on Rental Property Investing by Brandon Turner"),
        ("1119697026", "Mortgages for Dummies by Eric Tyson"),
        ("0812927427", "The Total Money Makeover by Dave Ramsey"),
        ("B09VW3KFVK", "Locking File Box for Mortgage and Financial Documents"),
        ("1260116050", "Home Buying Kit for Dummies"),
        ("0062953796", "Set for Life: Dominate Life, Money, and the American Dream"),
        ("B0B9PXKXB4", "Home Buyer's Checklist and Moving Planner Notebook"),
        ("1492368423", "The Millionaire Real Estate Investor by Gary Keller"),
        ("B07XKVJ4GB", "AmazonBasics Shredder for Sensitive Financial Documents"),
        ("1119860741", "First-Time Home Buyer: The Complete Playbook"),
    ],
    # ── Mental Health & Therapy ───────────────────────────────────────────────
    # CBT workbooks, journals, mindfulness tools, therapy-adjacent books
    "mental health & therapy": [
        ("1572245018", "The Mindfulness and Acceptance Workbook for Anxiety"),
        ("1626258406", "The Body Keeps the Score by Bessel van der Kolk"),
        ("1572244275", "DBT Skills Training Handouts and Worksheets"),
        ("B09B8LGTDG", "Anxiety Relief Journal with CBT Prompts and Mood Tracker"),
        ("0143133462", "Maybe You Should Talk to Someone by Lori Gottlieb"),
        ("1572246952", "The Anxiety and Worry Workbook by Clark and Beck"),
        ("B08FT3BK6S", "Aura Smart Sleep and Meditation Lamp"),
        ("1626254346", "Get Out of Your Mind and Into Your Life (ACT Workbook)"),
        ("0062409603", "First, We Make the Beast Beautiful by Sarah Wilson"),
        ("B09X7TQRQK", "Muse S Meditation and Sleep Headband"),
    ],
    # ── Pet Health & Vet Advice ───────────────────────────────────────────────
    # First aid, dental, joint supplements, flea prevention, enrichment
    "pet health & vet advice": [
        ("B09L3GS3L2", "Rayco First Aid Kit for Dogs and Cats"),
        ("B07FZBKKDQ", "Arm & Hammer Dog Dental Spray — No Brush Needed"),
        ("B07RJJ5RCG", "Zesty Paws Hip and Joint Supplement Chews for Dogs"),
        ("B00XNGNQ58", "FRONTLINE Plus Flea and Tick Treatment for Dogs"),
        ("B01BMKAGP2", "Thundershirt Classic Dog Anxiety Jacket"),
        ("B08KGQP5TH", "Purina Pro Plan Veterinary Supplements FortiFlora Probiotic"),
        ("B082PXQXJ1", "Nylabone Power Chew Durable Dog Chew Toys"),
        ("B07CNDMQ65", "PetSafe Easy Walk No-Pull Dog Harness"),
        ("B0091MOQVS", "Catit Flower Fountain — Cat Water Fountain"),
        ("B01LYNYJSC", "Midwest Homes Folding Metal Dog Crate"),
    ],
    # ── Small Business Finance ────────────────────────────────────────────────
    # Business books, accounting tools, organization, planning
    "small business finance": [
        ("1119475347", "Profit First by Mike Michalowicz"),
        ("1591845572", "The E-Myth Revisited by Michael Gerber"),
        ("0307465357", "The 4-Hour Work Week by Tim Ferriss"),
        ("B08MBTZJ7H", "Pendaflex Expandable File Organizer for Business Records"),
        ("1260455890", "QuickBooks Online: The Complete Guide"),
        ("B09JQLB8YD", "Adams Business Expense Record Book"),
        ("1118982428", "Financial Statements: A Step-by-Step Guide"),
        ("B0BFPD8FG3", "Avery Business Card Binder for Networking"),
        ("0399562990", "Traction: Get a Grip on Your Business by Gino Wickman"),
        ("B07XNV8KXN", "AmazonBasics 12-Sheet Cross-Cut Paper Shredder"),
    ],
}
# Partial-match aliases so any niche string resolves to a key above
NICHE_ALIAS = {
    "personal injury": "personal injury law",
    "injury law":      "personal injury law",
    "medicare":        "medicare & senior health insurance",
    "senior health":   "medicare & senior health insurance",
    "solar":           "residential solar & home energy",
    "home energy":     "residential solar & home energy",
    "home insurance":  "home insurance education",
    "homeowners":      "home insurance education",
    "mortgage":        "mortgage & home financing",
    "home financing":  "mortgage & home financing",
    "therapy":         "mental health & therapy",
    "mental health":   "mental health & therapy",
    "counseling":      "mental health & therapy",
    "pet health":      "pet health & vet advice",
    "pet doctor":      "pet health & vet advice",
    "veterinarian":    "pet health & vet advice",
    "small business":  "small business finance",
    "small biz":       "small business finance",
}

# ── AUTHORITATIVE REFERENCES (linked naturally in articles) ──────────────────

NICHE_REFERENCES = {
    "personal injury law": [
        ("the CDC's injury statistics", "https://www.cdc.gov/injury/wisqars/"),
        ("the American Bar Association's guidance", "https://www.americanbar.org/groups/public_education/"),
        ("Nolo's personal injury resources", "https://www.nolo.com/legal-encyclopedia/personal-injury"),
        ("the Insurance Information Institute", "https://www.iii.org/"),
    ],
    "medicare & senior health insurance": [
        ("Medicare.gov", "https://www.medicare.gov/"),
        ("the Centers for Medicare & Medicaid Services", "https://www.cms.gov/"),
        ("the State Health Insurance Assistance Program (SHIP)", "https://www.shiphelp.org/"),
        ("AARP's Medicare resource center", "https://www.aarp.org/health/medicare-insurance/"),
    ],
    "residential solar & home energy": [
        ("the Solar Energy Industries Association (SEIA)", "https://www.seia.org/"),
        ("the National Renewable Energy Laboratory (NREL)", "https://www.nrel.gov/"),
        ("EnergySage's market data", "https://news.energysage.com/"),
        ("the U.S. Department of Energy", "https://www.energy.gov/eere/solar/homeowners-guide-going-solar"),
    ],
    "home insurance education": [
        ("the Insurance Information Institute (III)", "https://www.iii.org/"),
        ("the National Association of Insurance Commissioners (NAIC)", "https://www.naic.org/"),
        ("your state's insurance department", "https://www.naic.org/state_web_map.htm"),
        ("the IBHS home fortification guides", "https://ibhs.org/"),
    ],
    "mortgage & home financing": [
        ("the Consumer Financial Protection Bureau (CFPB)", "https://www.consumerfinance.gov/owning-a-home/"),
        ("HUD-approved housing counselors", "https://www.hud.gov/i_want_to/talk_to_a_housing_counselor"),
        ("the Federal Housing Finance Agency (FHFA)", "https://www.fhfa.gov/"),
        ("Freddie Mac's home buyer resources", "https://myhome.freddiemac.com/"),
    ],
    "mental health & therapy": [
        ("the National Alliance on Mental Illness (NAMI)", "https://www.nami.org/"),
        ("SAMHSA's treatment locator", "https://findtreatment.gov/"),
        ("the 988 Suicide and Crisis Lifeline", "https://988lifeline.org/"),
        ("Psychology Today's therapist directory", "https://www.psychologytoday.com/us/therapists"),
    ],
    "pet health & vet advice": [
        ("the American Veterinary Medical Association (AVMA)", "https://www.avma.org/"),
        ("the ASPCA Poison Control Center", "https://www.aspca.org/pet-care/animal-poison-control"),
        ("PetMD's veterinary resource library", "https://www.petmd.com/"),
        ("the AAHA hospital accreditation standards", "https://www.aaha.org/"),
    ],
    "small business finance": [
        ("the U.S. Small Business Administration (SBA)", "https://www.sba.gov/"),
        ("the IRS small business tax center", "https://www.irs.gov/businesses/small-businesses-self-employed"),
        ("SCORE mentorship resources", "https://www.score.org/"),
        ("the Consumer Financial Protection Bureau's small business resources", "https://www.consumerfinance.gov/"),
    ],
}

# ── ARTICLE LENGTH TARGETS (by keyword priority) ─────────────────────────────

LENGTH_TARGETS = {
    "high":   {"min": 1600, "max": 2400, "label": "long-form"},
    "medium": {"min": 1000, "max": 1500, "label": "mid-length"},
    "low":    {"min":  700, "max": 1000, "label": "concise"},
}

# ── BUDGET MONITORING ─────────────────────────────────────────────────────────

def check_api_budget():
    try:
        r = requests.get(
            "https://api.anthropic.com/v1/usage",
            headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01"},
            timeout=10
        )
        if r.status_code == 200:
            spent = r.json().get("total_cost_usd", 0)
            pct   = (spent / ANTHROPIC_MONTHLY_BUDGET) * 100
            print(f"  Budget: ${spent:.2f} / ${ANTHROPIC_MONTHLY_BUDGET:.2f} ({pct:.0f}%)")
            if pct >= 90:
                print("BUDGET ALERT: 90% of monthly budget used -- stopping.")
                sys.exit(1)
            elif pct >= 75:
                print(f"  Budget warning: {pct:.0f}% used.")
    except Exception as e:
        print(f"  Budget check skipped: {e}")

# ── AMAZON AFFILIATE ──────────────────────────────────────────────────────────

def get_products_for_niche(niche: str, count: int = 3) -> list:
    niche_key = niche.lower().strip()
    products = NICHE_ASINS.get(niche_key)
    if not products:
        for alias, key in NICHE_ALIAS.items():
            if alias in niche_key:
                products = NICHE_ASINS.get(key, [])
                break
    if not products:
        return []
    shuffled = products[:]
    random.shuffle(shuffled)
    return shuffled[:count]


def build_affiliate_url(asin: str) -> str:
    tag = AMAZON_TRACKING_ID or "contentportfo-20"
    return f"https://www.amazon.com/dp/{asin}?tag={tag}"


def inject_affiliate_links(content: str, niche: str) -> str:
    """
    Inject 1 inline recommendation after the 2nd H2, plus a
    compact product box at the end. Max 3 products per article.
    Only runs when AMAZON_TRACKING_ID is set.
    """
    if not AMAZON_TRACKING_ID:
        return content

    products = get_products_for_niche(niche, 3)
    if not products:
        return content

    # Inline pick after 2nd H2
    asin0, name0 = products[0]
    url0 = build_affiliate_url(asin0)
    inline = (
        f"\n> **Helpful resource:** [{name0}]({url0}) is a top-rated option for this. "
        f"*(As an Amazon Associate this site earns from qualifying purchases.)*\n\n"
    )
    h2_count = 0
    lines = content.split("\n")
    new_lines = []
    injected = False
    for line in lines:
        new_lines.append(line)
        if line.startswith("## ") and not injected:
            h2_count += 1
            if h2_count == 2:
                new_lines.append(inline)
                injected = True
    content = "\n".join(new_lines)

    # Product box at end (2-3 items, subtle)
    rec = "\n\n## Helpful Resources\n\n"
    rec += "*As an Amazon Associate this site earns from qualifying purchases.*\n\n"
    for asin, name in products[:3]:
        url = build_affiliate_url(asin)
        rec += f"- **[{name}]({url})**\n"
    content += rec
    return content

# ── REFERENCE LINK INJECTION ──────────────────────────────────────────────────

def get_references_for_niche(niche: str) -> list:
    niche_key = niche.lower().strip()
    refs = NICHE_REFERENCES.get(niche_key)
    if not refs:
        for alias, key in NICHE_ALIAS.items():
            if alias in niche_key:
                refs = NICHE_REFERENCES.get(key, [])
                break
    return refs or []

# ── IMAGE FETCHING ────────────────────────────────────────────────────────────

def fetch_image_pexels(query: str, used_ids: set) -> dict | None:
    if not PEXELS_KEY:
        return None
    try:
        r = requests.get(
            "https://api.pexels.com/v1/search",
            headers={
                "Authorization": PEXELS_KEY,
                # Browser UA required — Cloudflare bot-challenges (error 1010 / 403) the
                # default python-requests UA under concurrent load, silently dropping images.
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
            },
            params={"query": query, "per_page": 80, "orientation": "landscape"},
            timeout=10
        )
        if r.status_code != 200:
            print(f"  Pexels non-200 ({r.status_code}) for '{query}'")
            return None
        if int(r.headers.get("X-Ratelimit-Remaining", 200)) < 5:
            return None
        photos = [p for p in r.json().get("photos", []) if str(p["id"]) not in used_ids]
        if not photos:
            return None
        # Wide random window (top 30 of 80) so consecutive articles don't land on
        # adjacent frames from the same shoot.
        photo = random.choice(photos[:30])
        used_ids.add(str(photo["id"]))
        def _clean(s: str) -> str:
            return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', str(s))
        return {
            "url":          _clean(photo["src"]["large"]),
            "credit":       _clean(photo["photographer"]),
            "credit_link":  _clean(photo["photographer_url"]),
            "source":       "pexels",
        }
    except Exception as e:
        print(f"  Pexels error: {e}")
        return None


def fetch_image_flux(query: str) -> dict | None:
    if not FLUX_KEY:
        return None
    try:
        r = requests.post(
            "https://fal.run/fal-ai/flux/schnell",
            headers={"Authorization": f"Key {FLUX_KEY}", "Content-Type": "application/json"},
            json={"input": {"prompt": f"Editorial photo: {query}. Natural lighting, no text, no watermarks.", "image_size": "landscape_16_9", "num_inference_steps": 4, "num_images": 1}},
            timeout=30
        )
        if r.status_code == 200:
            return {"url": r.json()["images"][0]["url"], "credit": None, "credit_link": None, "source": "flux"}
    except Exception as e:
        print(f"  Flux error: {e}")
    return None


_IMG_STOP = {
    "how","to","the","a","an","for","and","of","in","on","with","your","you","my",
    "best","top","good","great","guide","tips","tip","treatment","treatments","symptom","symptoms",
    "cost","costs","price","prices","pricing","vs","versus","why","what","when","where","which",
    "is","are","was","does","do","did","can","could","should","would","will","that","this",
    "safely","safe","fast","easy","near","me","explained","review","reviews","ultimate","complete",
    "step","steps","ways","way","things","need","needs","know","about","from","without","into",
    "after","before","during","using","use","get","make","fix","fixing","help","helping",
}

_DUP_STOP = {'how','to','a','an','the','for','in','on','of','and','or','your','you','with',
             'without','vs','best','guide','is','are','do','does','can','what','when','why',
             'my','it','at','be','this'}

def _slug_tokens(slug: str) -> frozenset:
    return frozenset(w for w in slug.split('-') if w and w not in _DUP_STOP)


def _derive_image_query(keyword: str) -> str:
    """Heuristic fallback: strip abstract/process words, keep the concrete subject nouns."""
    words = [w for w in re.findall(r"[A-Za-z]+", keyword.lower()) if w not in _IMG_STOP and len(w) > 2]
    return " ".join(words[:3]) if words else keyword


def fetch_image(query: str, used_ids: set) -> dict | None:
    img = fetch_image_pexels(query, used_ids)
    if img:
        return img
    print("  Pexels miss -- trying Flux...")
    return fetch_image_flux(query)

# ── PUBLISHED KEYWORD TRACKING ────────────────────────────────────────────────

def get_used_image_ids(repo: str) -> set:
    """Photo IDs of images already used by published articles - pre-seeds
    used_img_ids so a new article never reuses an image already on the site."""
    ids = set()
    try:
        from concurrent.futures import ThreadPoolExecutor
        r = requests.get(
            f"https://api.github.com/repos/{GITHUB_ORG}/{repo}/contents/content/posts",
            headers=GH_HEADERS, timeout=15)
        if r.status_code != 200:
            return ids
        files = [f for f in r.json() if isinstance(f, dict) and f["name"].endswith(".md")]
        def _scan(f):
            try:
                raw = requests.get(f["download_url"], timeout=10).text
                m = re.search(r'(?m)^image:\s*"?([^"\n]*)"?\s*$', raw)
                if m:
                    pid = re.search(r"/photos/(\d+)/", m.group(1))
                    return pid.group(1) if pid else None
            except Exception:
                return None
        with ThreadPoolExecutor(max_workers=10) as ex:
            for pid in ex.map(_scan, files):
                if pid:
                    ids.add(pid)
    except Exception as e:
        print(f"  used-image scan failed: {e}")
    return ids


def get_published_slugs(repo: str) -> set:
    """Fetch list of already-published article filenames from GitHub."""
    try:
        r = requests.get(
            f"https://api.github.com/repos/{GITHUB_ORG}/{repo}/contents/content/posts",
            headers=GH_HEADERS,
            timeout=15
        )
        if r.status_code == 200:
            return {f["name"].replace(".md", "") for f in r.json() if isinstance(f, dict)}
    except Exception:
        pass
    return set()


def keyword_to_slug(keyword: str) -> str:
    import re
    slug = keyword.lower()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"\s+", "-", slug.strip())
    return slug[:70]

# ── ARTICLE GENERATION ────────────────────────────────────────────────────────

def generate_article(keyword: str, site_config: dict, persona: dict, priority: str, voice_style: str = "") -> dict:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    _now          = datetime.now(timezone.utc)
    _current_date = _now.strftime("%B %Y")
    _current_year = _now.year

    niche         = site_config["niche"]
    affiliate_note = site_config.get("affiliate_note", "")
    site_persona  = SITE_PERSONAS.get(site_config.get("repo", ""), {})
    tone          = site_persona.get("tone", "")
    ymyl          = site_persona.get("ymyl", False)
    references    = get_references_for_niche(niche)
    length_target = LENGTH_TARGETS.get(priority, LENGTH_TARGETS["medium"])

    # Pick 1-2 reference links to weave in naturally
    ref_instruction = ""
    if references:
        picked_refs = random.sample(references, min(2, len(references)))
        ref_links = ", ".join(f'[{name}]({url})' for name, url in picked_refs)
        ref_instruction = (
            f"- Naturally reference 1-2 authoritative sources in the text: {ref_links}. "
            "Do not list them -- weave them into a sentence as supporting evidence."
        )

    ymyl_instruction = ""
    if ymyl:
        ymyl_instruction = (
            "- This is a YMYL (Your Money / Your Life) topic. Be factually accurate, "
            "balanced, and note that professional consultation is always advisable. "
            "Do not make guarantees or quote specific dollar figures as typical outcomes."
        )

    voice_instruction = f"\n## Your writing voice for this article:\n{voice_style}\n" if voice_style else ""

    system_prompt = f"""{tone}

You are writing an article for a {niche} website in the voice of {persona['name']}. Do NOT include a byline, author name, or any "By ..." line anywhere in the article body — the byline is added automatically by the site template.
{voice_instruction}
Length target: {length_target['min']}-{length_target['max']} words ({length_target['label']}).

Temporal context (critical):
- Today is {_current_date}. The current year is {_current_year}.
- Do NOT cite a specific past year (e.g. "in 2024") as if it describes the present. If you mean now, it is {_current_year}.
- Prefer "currently", "today", or "as of this year", or omit the year entirely, rather than pinning a stale year.
- Never reference a year later than {_current_year}.

Writing rules (follow every one):
- Write like a specific human with opinions, not a neutral summarizer. Where the topic allows, take a clear stance ("honestly, I'd skip the pricey version", "most advice on this is wrong"). Readers trust a writer who commits.
- Vary your rhythm hard. Mix short, punchy sentences (fragments are fine) with longer ones. Let some paragraphs be a single sentence. Do not make every section the same length.
- Open differently every time. Not always a "scenario." Sometimes a blunt claim, a specific number, a confession, a question, or one concrete moment. Never open with "In today's...", "When it comes to...", "In the world of...", or "Imagine...".
- Be relentlessly specific. Name real products, brands, prices, dates, places, and numbers. "$180 a month" beats "expensive"; "a 2019 JAMA study" beats "studies show." Specificity is the single biggest tell of real writing.
- Use concrete first-person experience naturally ("the first time I tried this", "a reader emailed me last week", "I made this mistake myself"). Include at least 1-2 "I tested", "in my experience", or "when I [verb]" moments per article. These signal authentic expertise and are the highest-trust differentiator from generic AI writing. Do not overdo it—2-3 natural moments per article is perfect, not contrived.
- Never use em dashes (-- or ---). Use commas, colons, parentheses, or a new sentence.
- Use contractions everywhere (you'll, it's, don't, can't, that's).
- Allow mild informality, the occasional aside, and a rhetorical question now and then. Real people digress a little.
- Acknowledge nuance and uncertainty honestly ("the research here is mixed", "this won't work for everyone") instead of false confidence or robotic both-sidesing.
- Where appropriate, challenge conventional wisdom with data backing you up. Readers trust writers who say "everyone says X, but that's actually wrong because Y." Back contrarian claims with specific evidence.
- Admit what you don't know or what the data doesn't show. Examples: "I don't have good numbers on Z, so I can't speak to it confidently." This honesty builds trust more than false certainty.
- Include 1-2 moments where you or the reader would get it wrong at first, then reveal the actual answer. Example: "I thought Y for years until I realized X. Here's what changed my mind." Vulnerability signals authenticity.
- Use specific timestamps, dates, and named references (not generic "recently"). "In March 2024, when X happened..." or "Sarah, a reader from Denver, told me..." makes it feel real, not templated.

NEVER use these words/phrases (dead giveaways of AI writing): delve, dive into, navigate, navigating, realm, landscape, tapestry, journey, embark, robust, leverage, seamless, elevate, unlock, harness, foster, cultivate, crucial, essential, vital, pivotal, holistic, myriad, plethora, testament, underscore, game-changer, in conclusion, in summary, it's worth noting, it is important to note, that said, ultimately, at the end of the day, ever-evolving, when it comes to, rest assured, look no further, the bottom line, first and foremost, moreover, furthermore, firstly, secondly, in today's fast-paced world.

Avoid these structural tells:
- The rule of three in every sentence ("X, Y, and Z"). Sometimes list two things, sometimes five.
- "It's not just X, it's Y" / "It's not about X, it's about Y" constructions.
- Ending the article, or every section, by restating what you just said.
- Perfectly balanced "on one hand / on the other hand" hedging on everything.
- Opening every paragraph with its topic sentence. Sometimes bury the point.
- Turning everything into a bulleted list. Prose is fine; use a list only when it genuinely helps.
- Feeling overly polished or templated. Good writing has rough edges: an unfinished thought that resolves, a tangent that circles back, a parenthetical aside that reveals personality.
- Making every section the exact same tone. Vary: passionate in one section, matter-of-fact in another, skeptical in a third.
- Anticipating reader skepticism and addressing it directly. If you're making a bold claim, imagine what a reader would think ("you're probably thinking 'that can't be right'") and show why it is. This conversational pushback feels human.
{ymyl_instruction}
{ref_instruction}
{affiliate_note}

Output ONLY the article body in Markdown. Start with a normal paragraph, never a heading, and do not restate or rephrase the title as an opening line. Never use a level-1 "# " heading anywhere in the article; every section heading must be "## " or smaller. The page title is rendered separately from frontmatter, so a "# " heading here creates a duplicate oversized title."""

    user_prompt = f"""Write a thorough, genuinely useful article about: {keyword}

Loose structure (vary it -- do not make every article identical):
- Open without a heading, and vary how you open (see the writing rules).
- Cover the topic across a handful of H2 sections, but vary how many, how long they run, and how you approach each. Some can be a few tight paragraphs; let one go deeper.
- Where it genuinely helps, include a step-by-step walkthrough or a comparison, but do not force a list onto everything.
- Near the end, add a short FAQ: 4-5 real questions readers actually ask, each as an H3 ending in a question mark, with a direct 1-3 sentence answer. (Keep these -- they power our FAQ feature.)
- Include a "## Sources" section before the FAQ or after the last main section. List 3-5 authoritative sources you cite in the article: research papers, industry reports, official resources, or verified data. Format: "- [Source name]: [brief description]" or "[Source name] ([year]): [description]".
- Weave in 2-3 worked examples with concrete outcomes. Format: "[Scenario] → [Action taken] → [Result with numbers]". Example: "When we tested this approach on 50 projects, completion time dropped 28%, from 45 days to 32 days." Use real numbers even if estimated from industry experience.
- Close naturally -- no "Conclusion" or "Summary" heading, and do not restate everything.

**Currency & authenticity signal:** Include an explicit "as of {_current_year}" or "current as of {_current_date}" statement where it fits naturally. This signals freshness and expert confidence, not a stale regurgitation. Examples: "As of June 2026, solar installations...", "Current rates (June 2026)...", "This year's market shows..."

Write it in the author's specific voice, with the opinions and concrete detail only someone who knows this topic firsthand would include.

**CRITICAL: Author Voice Distinctiveness**

Each author should sound like a different human. They use different sentence patterns, favorite words, and emotional tones. The readers should be able to tell them apart by voice alone.

Examples of voice variation (NOT "professional neutral"):
- A former adjuster (insurance): skeptical, dry humor, calls out industry BS, loves numbers
- An RV lifer (travel): casual, tangent-prone ("anyway, back to the point"), enthusiastic but honest about downsides
- A vet tech (pet care): clinical but warm, references specific breed behaviors like "anyone who's owned a Dachshund knows..."
- A small-business CFO (finance): blunt, impatient with buzzwords, "let me show you the numbers that matter"
- A therapist educator (mental health): validating, acknowledges complexity, checks assumptions

Your job: Read the persona's tone/background and write in THAT voice. Not "professional." Not "neutral." THAT SPECIFIC PERSON. If they'd digress, digress. If they'd be skeptical, be skeptical. If they'd use casual language, use it.
"""

    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4000,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}]
    )
    content = msg.content[0].text

    # Meta description + subject-accurate hero image query (single call)
    meta = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=160,
        messages=[{"role": "user", "content": (
            f"For an article titled '{keyword}', output ONLY a JSON object (no preamble, no code fence):\n"
            '{"description": "<140-155 char SEO meta description, plain text, no quotes>", '
            '"image_query": "<2-4 word concrete photographable subject for the hero photo. Name the specific '
            'animal, object, person, or scene the article is about. If the title names an animal (cat, dog, '
            'chicken, etc.), the query MUST lead with that exact animal. AVOID abstract words like treatment, '
            'cost, guide, symptoms, tips, fear, recovery, safely.>"}'
        )}]
    )
    _mtext = meta.content[0].text.strip()
    description = ""
    image_query = ""
    try:
        _mj = json.loads(re.search(r"\{.*\}", _mtext, re.S).group(0))
        description = str(_mj.get("description", "")).strip()
        image_query = str(_mj.get("image_query", "")).strip()
    except Exception:
        pass
    # Fallback description cleanup (preserve old robustness against leaked preambles)
    if not description:
        _raw = re.sub(r"^\s*(here'?s?[^:\n]*:|sure[,!:]\s*|certainly[,!:]?\s*|okay[,!:]?\s*)", "", _mtext, flags=re.I).strip()
        _lines = [l.strip().strip('"').strip("'") for l in _raw.split("\n") if l.strip()]
        _clean = [l for l in _lines if not re.search(r"meta description|character range|^here\b|^sure\b|^certainly\b", l, re.I)]
        description = (_clean[0] if _clean else (_lines[0] if _lines else _raw))
    description = description.strip().strip('"').strip("'")[:160]
    if not image_query:
        image_query = _derive_image_query(keyword)

    return {"content": content, "description": description, "image_query": image_query}

# ── MARKDOWN BUILDER ──────────────────────────────────────────────────────────

_FAQ_STRIP_RE = re.compile(
    r'\n#{2,3}[ \t]*(?:FAQs?\b|Frequently Asked|Common Questions|Questions People|Q\s*&\s*A)[^\n]*\n'
    r'[\s\S]*?'
    r'(?=\n#{1,2}[ \t]|\n---\n|\n\*Photo:|\Z)', re.IGNORECASE)

def _strip_faq_body(content):
    """Remove the inline FAQ section (## FAQ + ### Q&A) from the body. Stops at the next
    top-level heading, an hr, the photo credit, or end — so closing prose/credits survive."""
    return _FAQ_STRIP_RE.sub('\n', content, count=1)

def _extract_faqs(body):
    """Extract FAQ Q&A pairs (### Question? + answer) from article body for FAQPage schema."""
    import re as _re
    def _sm(t):
        t = _re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
        t = _re.sub(r"[*_`#>]", "", t)
        return _re.sub(r"\s+", " ", t).strip()
    lines = body.split("\n"); faqs = []; i = 0
    while i < len(lines):
        m = _re.match(r"^###\s+(.+\?)\s*$", lines[i].strip())
        if m:
            q = _sm(m.group(1)); ans = []; i += 1
            while i < len(lines):
                ln = lines[i].strip()
                if _re.match(r"^#{1,6}\s", ln) or ln.startswith("---") or ln.startswith("*Photo:") or ln.startswith("!["):
                    break
                if ln: ans.append(ln)
                i += 1
            a = _sm(" ".join(ans))[:600]
            if q and len(a) > 20:
                faqs.append((q.replace('"', "'"), a.replace("\\", "").replace('"', "'")))
        else:
            i += 1
    return faqs[:6]



# -- Title cap fixes -----------------------------------------------------------
_TITLE_CAP_FIXES = [
    (r'\bRving\b','RVing'),(r'\bRvs\b','RVs'),(r'\bRv\b','RV'),
    (r'\bHvac\b','HVAC'),(r'\bHoa\b','HOA'),(r'\bPvc\b','PVC'),(r'\bPex\b','PEX'),
    (r'\bDiy\b','DIY'),(r'\bApr\b','APR'),(r'\bFha\b','FHA'),(r'\bPiti\b','PITI'),
    (r'\bLtv\b','LTV'),(r'\bDti\b','DTI'),(r'\bLlc\b','LLC'),(r'\bIrs\b','IRS'),
    (r'\bEin\b','EIN'),(r'\bSba\b','SBA'),(r'\bRoi\b','ROI'),
    (r'\bSeo\b','SEO'),(r'\bApi\b','API'),(r'\bGps\b','GPS'),(r'\bAi\b','AI'),
    (r'\bRpg\b','RPG'),(r'\bFps\b','FPS'),(r'\bNpcs\b','NPCs'),(r'\bNpc\b','NPC'),
    (r'\bGdc\b','GDC'),(r'\bPax\b','PAX'),(r'\bSdk\b','SDK'),
    (r'\bUi\b','UI'),(r'\bUx\b','UX'),
    (r'\bPtsd\b','PTSD'),(r'\bOcd\b','OCD'),(r'\bCbt\b','CBT'),
    (r'\bDbt\b','DBT'),(r'\bEmdr\b','EMDR'),(r'\bAdhd\b','ADHD'),
    (r'\bAed\b','AED'),(r'\bCpr\b','CPR'),
    (r'\bUsda\b','USDA'),(r'\bFda\b','FDA'),(r'\bCdc\b','CDC'),
    (r'\bAspca\b','ASPCA'),(r'\bCms\b','CMS'),(r'\bSsa\b','SSA'),
    (r'\bDwi\b','DWI'),(r'\bDui\b','DUI'),
    (r'\bKwh\b','kWh'),(r'\bKw\b','kW'),
    (r'\bFaq\b','FAQ'),(r'\bTv\b','TV'),(r'\bHr\b','HR'),(r'\bPto\b','PTO'),
]
_TITLE_CAP_RX = [(re.compile(p), r) for p, r in _TITLE_CAP_FIXES]

def _fix_title_caps(title):
    for rx, r in _TITLE_CAP_RX:
        title = rx.sub(r, title)
    return title

def build_markdown(
    keyword: str,
    article: dict,
    image: dict | None,
    categories: list,
    tags: list,
    persona: dict,
    ymyl: bool,
    disclaimer: str,
    title_override: str = None,
) -> str:
    slug = keyword_to_slug(keyword)
    _title = _fix_title_caps(title_override) if title_override else _fix_title_caps(keyword.title())
    _title = _title.replace('"', "'")
    date = datetime.now(timezone.utc).isoformat()
    image_url = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', image["url"]) if image else ""

    content = article["content"]

    # Append photo credit
    if image and image.get("credit") and image.get("credit_link"):
        content += f"\n\n*Photo: [{image['credit']}]({image['credit_link']}) via Pexels*"

    # Append YMYL disclaimer
    if ymyl and disclaimer:
        content += f"\n\n---\n\n{disclaimer}"

    frontmatter = f"""---
title: "{_title}"
date: {date}
draft: false
description: "{article['description']}"
image: "{image_url}"
categories: {json.dumps(categories)}
tags: {json.dumps(tags)}
author: "{persona['name']}"
author_slug: "{persona.get('slug', '')}"
author_title: "{persona.get('title', '')}"
author_bio: "{persona['bio']}"
slug: "{slug}"
affiliate_disclosure: {"true" if AMAZON_TRACKING_ID else "false"}
---

"""
    # Inject FAQs (extracted from the article body) into frontmatter for FAQPage schema.
    # Fail-safe: never let FAQ handling break publishing.
    try:
        _faqs = _extract_faqs(content)
        if _faqs:
            _fy = "faqs:\n"
            for _q, _a in _faqs:
                _fy += f'  - q: "{_q}"\n    a: "{_a}"\n'
            frontmatter = frontmatter.replace("\n---\n\n", "\n" + _fy + "---\n\n", 1)
            # Remove the inline FAQ from the body — the styled template FAQ section
            # renders from the `faqs:` frontmatter, so keeping the body copy duplicates it.
            content = _strip_faq_body(content)
    except Exception:
        pass
    result = frontmatter + content
    # Defensive: verify the generated markdown has proper YAML frontmatter.
    # Strategy: extract the YAML section (up to the FIRST \n---\n in file), then
    # check that it doesn't contain body content merged in (Bug A pattern).
    # A naive split on \n---\n is insufficient because article bodies often contain
    # --- horizontal rules, which would satisfy the check even for broken articles.
    _ym = re.match(r'^---\n(.*?)\n---(?:\r?\n|$)', result, re.DOTALL)
    if not _ym:
        raise ValueError(f"build_markdown: missing frontmatter closing '---' delimiter. "
                         f"First 200 chars: {result[:200]!r}")
    _yaml_section = _ym.group(1)
    _bug_a = re.search(r'\n\w+: (?:true|false), ', '\n' + _yaml_section)
    if _bug_a:
        raise ValueError(f"build_markdown: Bug A detected — boolean YAML field followed by body content "
                         f"(missing closing ---). Match: {_bug_a.group()!r}")
    return result

# ── GITHUB COMMIT ─────────────────────────────────────────────────────────────

def commit_to_github(repo: str, filename: str, content: str, message: str) -> bool:
    url = f"https://api.github.com/repos/{GITHUB_ORG}/{repo}/contents/content/posts/{filename}"
    sha = None
    r = requests.get(url, headers=GH_HEADERS)
    if r.status_code == 200:
        sha = r.json()["sha"]
    payload = {
        "message": message,
        "content": base64.b64encode(content.encode()).decode(),
    }
    if sha:
        payload["sha"] = sha
    r = requests.put(url, headers=GH_HEADERS, json=payload, timeout=30)
    return r.status_code in [200, 201]

# ── SITEMAP SUBMISSION ────────────────────────────────────────────────────────

def submit_sitemap(domain: str):
    token = os.environ.get("GOOGLE_ACCESS_TOKEN")
    if not token:
        return
    sitemap_url = f"https://{domain}/sitemap.xml"
    url = f"https://www.googleapis.com/webmasters/v3/sites/https%3A%2F%2F{domain}%2F/sitemaps/{requests.utils.quote(sitemap_url, safe='')}"
    r = requests.put(url, headers={"Authorization": f"Bearer {token}"}, timeout=15)
    print(f"  Sitemap: {'submitted' if r.status_code in [200,204] else 'skipped (' + str(r.status_code) + ')'}")


# ── GSC INDEXING API SUBMISSION ───────────────────────────────────────────────
# Submits newly created article URLs to Google's Indexing API so they begin
# indexing shortly after publication, instead of waiting for the daily backfill.
# NOTE: the Indexing API quota is 200 requests/day PER Google Cloud project, and
# all sites share one project — so this is rate-limit aware: it stops cleanly on
# 429/403 (quota) and lets the daily backfill task pick up anything it skips.

def _indexing_access_token():
    refresh = os.environ.get("GSC_INDEXING_TOKEN", "")
    cid     = os.environ.get("GOOGLE_CLIENT_ID", "")
    csec    = os.environ.get("GOOGLE_CLIENT_SECRET", "")
    if not (refresh and cid and csec):
        return None
    try:
        r = requests.post("https://oauth2.googleapis.com/token", data={
            "client_id":     cid,
            "client_secret": csec,
            "refresh_token": refresh,
            "grant_type":    "refresh_token",
        }, timeout=15)
        if r.status_code == 200:
            return r.json().get("access_token")
        print(f"  Indexing: token exchange failed ({r.status_code})")
    except Exception as e:
        print(f"  Indexing: token error {e}")
    return None


def submit_to_indexing(urls: list):
    """Submit new article URLs to the Google Indexing API (shared 200/day quota)."""
    urls = [u for u in urls if u]
    if not urls:
        return
    token = _indexing_access_token()
    if not token:
        print("  Indexing: skipped (no GSC_INDEXING_TOKEN)")
        return
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    ok = 0
    for u in urls:
        try:
            r = requests.post(
                "https://indexing.googleapis.com/v3/urlNotifications:publish",
                headers=headers, json={"url": u, "type": "URL_UPDATED"}, timeout=15,
            )
            if r.status_code == 200:
                ok += 1
            elif r.status_code in (429, 403) and ("quota" in r.text.lower() or "rateLimit" in r.text):
                print(f"  Indexing: daily quota reached after {ok} URLs (daily task will catch the rest)")
                break
            else:
                print(f"  Indexing: {r.status_code} for {u}")
        except Exception as e:
            print(f"  Indexing: error {e}")
    print(f"  Indexing: submitted {ok}/{len(urls)} new URLs to Google")

# ── KEYWORD LOADER ────────────────────────────────────────────────────────────

def load_keywords(repo: str) -> list:
    """Load keywords.csv from GitHub repo, sorted high > medium > low priority."""
    r = requests.get(
        f"https://api.github.com/repos/{GITHUB_ORG}/{repo}/contents/keywords.csv",
        headers=GH_HEADERS
    )
    if r.status_code != 200:
        return []
    content = base64.b64decode(r.json()["content"]).decode()
    reader = csv.DictReader(io.StringIO(content))
    rows = list(reader)
    # Shuffle first, then STABLE-sort by priority: high-priority still publishes first,
    # but topics are randomized within each tier so a batch doesn't pull a run of
    # consecutive same-category keywords (which clustered same-topic articles by date).
    random.shuffle(rows)
    order = {"high": 0, "medium": 1, "low": 2}
    rows.sort(key=lambda x: order.get(x.get("priority", "low").lower(), 2))
    return rows

# ── MAIN PUBLISHER ────────────────────────────────────────────────────────────

# ── TOPICAL (Track B): trend-aware, web-researched article generation ──────────
NICHE_COMMUNITIES = {
    "keto-living-guide":       {"subs": "r/keto, r/ketorecipes, r/xxketo", "scope": "ketogenic and low-carb diet: nutrition science, recipes, products, health research, weight loss"},
    "solar-home-planner":      {"subs": "r/solar, r/SolarDIY", "scope": "residential solar power: panels, costs, tax credits and incentives, installation, home batteries"},
    "solar-planner-guide":     {"subs": "r/solar, r/SolarDIY", "scope": "home solar planning: system sizing, ROI, net metering, incentive and policy changes"},
    "pet-doctor-guide":        {"subs": "r/AskVet, r/pets, r/dogs, r/cats", "scope": "pet health and veterinary care: symptoms, treatments, nutrition, preventive care, product recalls"},
    "rv-life-guide":           {"subs": "r/RVLiving, r/GoRVing, r/vandwellers", "scope": "RV and motorhome living: travel, maintenance, campgrounds, gear, full-time RV life"},
    "mortgage-advisor-guide":  {"subs": "r/Mortgages, r/FirstTimeHomeBuyer, r/RealEstate", "scope": "mortgages and home financing: rates, loan types, refinancing, first-time buyer programs"},
    "home-insurance-guide":    {"subs": "r/Insurance, r/homeowners", "scope": "homeowners insurance: coverage, claims, premiums, disaster policy, rate and market trends"},
    "small-biz-finance-guide": {"subs": "r/smallbusiness, r/Entrepreneur, r/Bookkeeping", "scope": "small business finance: funding, taxes, accounting, cash flow, LLC and tax-law changes"},
    "seniorstrength":          {"subs": "r/Fitness, r/flexibility", "scope": "strength and fitness for adults over 60: exercise, mobility, balance, injury prevention, healthy aging"},
    "chicken-keeper-guide":    {"subs": "r/BackYardChickens, r/chickens", "scope": "backyard chicken keeping: coops, breeds, egg production, flock health, avian flu and poultry news"},
    "fixitrightway":           {"subs": "r/HomeImprovement, r/DIY, r/Fixit", "scope": "home repair and DIY: tools, materials, projects, costs, when to DIY vs hire a pro"},
    "injury-victim-guide":     {"subs": "r/legaladvice, r/personalinjury", "scope": "personal injury and accident claims: the legal process, settlements, insurance, victim rights (general information, not legal advice)"},
    "medicare-starter":        {"subs": "r/medicare, r/HealthInsurance", "scope": "Medicare: enrollment, plan types, Parts A/B/C/D, costs, annual enrollment and policy changes"},
    "therapy-finder-guide":    {"subs": "r/therapy, r/mentalhealth, r/askatherapist", "scope": "mental health and therapy: modalities like CBT, DBT, and EMDR, finding a therapist, insurance, wellbeing"},
    "gamedevproducer":         {"subs": "r/gamedev, r/IndieDev, r/gamedesign", "scope": "indie and studio game development: production, publishing, funding, engines, releases, layoffs, GDC and industry events"},
}

_TOPICAL_RULES = (
    "Writing rules: natural human voice; vary sentence length; NEVER use em dashes (use commas, colons, or a new sentence); "
    "avoid AI cliches (In conclusion, It's worth noting, Delve into, Navigating, Moreover, Furthermore); use contractions; "
    "be specific with the real numbers, names, and dates from the research; occasional first person is fine."
)

def _parse_json_block(text):
    text = re.sub(r'^```(?:json)?\s*', '', text.strip()); text = re.sub(r'\s*```$', '', text)
    s = text.find('{')
    if s == -1:
        return None
    d = 0
    for i in range(s, len(text)):
        if text[i] == '{':
            d += 1
        elif text[i] == '}':
            d -= 1
            if d == 0:
                try:
                    return json.loads(text[s:i+1])
                except Exception:
                    return None
    return None

def generate_topical_article(site_config: dict, persona: dict, voice_style: str = "", recent_topics=None):
    """Discover a current topic via web search, research it, and write a timely article.
    Returns dict(keyword, content, description, image_query, category) or None on any failure."""
    repo = site_config.get("repo", "")
    comm = NICHE_COMMUNITIES.get(repo)
    if not comm:
        return None
    recent_topics = recent_topics or []
    sp = SITE_PERSONAS.get(repo, {})
    tone = sp.get("tone", "")
    ymyl = sp.get("ymyl", False)
    now = datetime.now(timezone.utc)
    current_date = now.strftime("%B %Y")
    current_year = now.year
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    scope_lead = comm["scope"].split(":")[0]
    recent_blurb = ("; ".join(recent_topics[:30])) or "none yet"

    radar_prompt = f"""You are a content strategist for a website about {comm['scope']}.
Today is {current_date}.

Find ONE genuinely CURRENT, topical angle for a new article: something tied to recent news, a fresh trend, a new release/product/policy, a seasonal moment, or an active community discussion happening RIGHT NOW ({current_date}). It must NOT be a generic evergreen how-to. It should feel timely and worth reading this month.

Search the web for {scope_lead} news and developments from the last 1 to 3 months. Also weigh what people are actively discussing in communities like {comm['subs']}.

Do NOT pick anything close to these already-covered topics: {recent_blurb}.

When done researching, output ONLY a JSON object (no prose before or after):
{{
  "title": "A concise specific Title Case headline UNDER 70 characters. No em dashes. No clickbait. Omit year unless essential.",
  "angle": "1-2 sentences: the timely hook and why it matters now",
  "key_facts": ["3-6 concrete CURRENT facts with specifics: numbers, dates, names"],
  "sources": [{{"title": "source name", "url": "https://...", "published": "approx date"}}],
  "image_query": "2-4 word hero photo search query"
}}"""

    try:
        msg = client.messages.create(
            model="claude-sonnet-4-6", max_tokens=2500,
            tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}],
            messages=[{"role": "user", "content": radar_prompt}],
        )
    except Exception as e:
        print(f"    [topical] radar error: {e}")
        return None

    brief_text = "".join(getattr(b, "text", "") for b in msg.content if getattr(b, "type", "") == "text")
    brief = _parse_json_block(brief_text)
    if not brief or not brief.get("title") or not brief.get("key_facts"):
        print("    [topical] no usable brief; skipping")
        return None

    title = _fix_title_caps(brief["title"].replace("—", ", ").replace("  ", " ").strip())
    facts = "\n".join(f"- {f}" for f in brief.get("key_facts", []))
    srcs = brief.get("sources", []) or []
    src_lines = "\n".join(f"- {s.get('title','source')} | {s.get('url','')} | {s.get('published','')}" for s in srcs)
    ymyl_note = ("\n- This is a YMYL topic. Be accurate and balanced and note that professional consultation is advisable. Do not give guarantees or specific personal advice.") if ymyl else ""
    voice_note = f"\nYour writing voice: {voice_style}\n" if voice_style else ""

    system = f"""{tone}

You are writing a TIMELY, topical article (not an evergreen how-to). Today is {current_date}. The current year is {current_year}. Never reference a year later than {current_year}.
{voice_note}
Length target: 900-1400 words.

{_TOPICAL_RULES}{ymyl_note}

Output ONLY the article body in Markdown. Start with a normal paragraph, never a heading. Never use a level-1 "# " heading anywhere; all section headings must be "## " or smaller (the title comes from frontmatter and is rendered separately, so a "# " here creates a duplicate oversized title)."""

    user = f"""Write a current, topical article.

TITLE: {title}
TIMELY ANGLE: {brief.get('angle','')}

CURRENT RESEARCH (real and current, ground every claim in these, invent nothing):
{facts}

SOURCES (weave 2-3 inline as evidence, then list ALL in a Sources section):
{src_lines}

Structure:
1. Open with the timely hook: what's happening now and why the reader should care. No heading.
2. 3-5 H2 sections of analysis and practical takeaways. Explainer/analysis, NOT a numbered how-to.
3. Weave 2-3 sources inline naturally.
4. A short closing paragraph (no heading).
5. A final section titled exactly "## Sources" listing every source as: - [title](url) (published date)."""

    try:
        wmsg = client.messages.create(
            model="claude-sonnet-4-6", max_tokens=4000, system=system,
            messages=[{"role": "user", "content": user}],
        )
    except Exception as e:
        print(f"    [topical] write error: {e}")
        return None

    article_md = "".join(getattr(b, "text", "") for b in wmsg.content if getattr(b, "type", "") == "text")
    article_md = article_md.replace("—", ", ").strip()
    if len(article_md) < 600:
        print("    [topical] write too short; skipping")
        return None

    try:
        dmsg = client.messages.create(
            model="claude-sonnet-4-6", max_tokens=80,
            messages=[{"role": "user", "content": f"Write a 140-155 character SEO meta description for an article titled '{title}'. Plain text, no quotes."}],
        )
        description = dmsg.content[0].text.strip().replace('"', "'")[:160]
    except Exception:
        description = (brief.get("angle", "") or title)[:155]

    print(f"    [topical] ok: {title}")
    return {"keyword": title, "content": article_md, "description": description,
            "image_query": brief.get("image_query") or title, "category": "trending"}


def publish_site(site_name: str, count: int):
    if site_name not in SITES:
        print(f"Site '{site_name}' not in SITES_CONFIG")
        return

    site = SITES[site_name]
    repo = site["repo"]
    niche = site["niche"]
    print(f"\nPublishing {count} articles to {site_name} ({site['domain']})")

    # Load keywords + already-published slugs
    keywords       = load_keywords(repo)
    published      = get_published_slugs(repo)
    ymyl           = SITE_PERSONAS.get(repo, {}).get("ymyl", False)
    disclaimer     = SITE_PERSONAS.get(repo, {}).get("disclaimer", "")
    # Round-robin offset: continues rotation from where previous publish runs left off
    voice_offset   = len(published)

    if not keywords:
        print(f"  No keywords found")
        return

    # Skip already-published keywords
    unpublished = [
        kw for kw in keywords
        if keyword_to_slug(kw.get("keyword", "")) not in published
    ]

    # Skip NEAR-duplicate keywords (slug-token Jaccard >= 0.8 vs any published slug).
    # Prevents the same topic publishing twice under a different slug (a "low value" trigger).
    _pub_tok = [_slug_tokens(s) for s in published]
    def _near_dup(kw):
        t = _slug_tokens(keyword_to_slug(kw.get("keyword", "")))
        if not t:
            return False
        for pt in _pub_tok:
            if pt and len(t & pt) / len(t | pt) >= 0.8:
                return True
        return False
    _before = len(unpublished)
    unpublished = [kw for kw in unpublished if not _near_dup(kw)]
    if len(unpublished) < _before:
        print(f"  Skipped {_before - len(unpublished)} near-duplicate keyword(s)")

    if not unpublished:
        print(f"  All keywords already published!")
        return

    to_publish   = unpublished[:count]
    used_img_ids = get_used_image_ids(repo)
    if used_img_ids:
        print(f"  Pre-seeded {len(used_img_ids)} existing image IDs for dedup")
    new_urls     = []   # URLs of articles created this run, for Indexing API submission

    # Load site-specific authors for round-robin persona selection
    _af = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'authors.json')
    _site_authors = json.load(open(_af, encoding='utf-8')) if os.path.exists(_af) else []

    for i, kw_row in enumerate(to_publish, 1):
        keyword  = kw_row.get("keyword", "")
        category = kw_row.get("category", niche)
        priority = kw_row.get("priority", "medium").lower()
        # Round-robin through portfolio voices; offset by published count so rotation
        # continues correctly across daily runs (article 201 picks up where 200 left off)
        voice    = PORTFOLIO_VOICES[(voice_offset + i - 1) % len(PORTFOLIO_VOICES)]
        if _site_authors:
            persona = _site_authors[(voice_offset + i - 1) % len(_site_authors)]
        else:
            persona  = {"name": voice["name"], "bio": voice["bio"]}

        # ── Track B: every 3rd article is topical (trend-aware, web-researched) ──
        topical = None
        if (voice_offset + i) % 3 == 0:
            try:
                _recent = [s.replace('-', ' ') for s in list(published)[:40]]
                topical = generate_topical_article(site, persona, voice_style=voice["style"], recent_topics=_recent)
                if topical and keyword_to_slug(topical["keyword"]) in published:
                    print("    Topical topic already covered; using evergreen")
                    topical = None
            except Exception as _te:
                print(f"    Topical generation failed ({_te}); falling back to evergreen")
                topical = None

        if topical:
            keyword   = topical["keyword"]
            category  = topical.get("category", "trending")
            img_query = topical.get("image_query") or keyword
            print(f"\n  [{i}/{count}] TOPICAL: {keyword} (author: {persona['name']})")
        else:
            img_query = None  # set after generation, from the article's own image_query
            print(f"\n  [{i}/{count}] {keyword} (priority: {priority}, author: {persona['name']})")

        try:
            check_api_budget()

            if topical:
                article = {"content": topical["content"], "description": topical["description"]}
            else:
                # Generate article with portfolio voice style
                article = generate_article(keyword, site, persona, priority, voice_style=voice["style"])
            print(f"    Article: {len(article['content'])} chars")

            # Evergreen: use the article's subject-accurate image query (set post-generation)
            if not topical and not img_query:
                img_query = article.get("image_query") or _derive_image_query(keyword)

            # Inject affiliate links
            article["content"] = inject_affiliate_links(article["content"], niche)

            # Fetch image
            image = fetch_image(img_query, used_img_ids)
            print(f"    Image: {'ok' if image else 'none'}")

            # Build markdown
            tags = [w.lower() for w in keyword.split() if len(w) > 3][:5]
            markdown = build_markdown(
                keyword, article, image,
                categories=[category],
                tags=tags,
                persona=persona,
                ymyl=ymyl,
                disclaimer=disclaimer,
                title_override=(keyword if topical else None),
            )

            # Commit
            filename  = keyword_to_slug(keyword) + ".md"
            committed = commit_to_github(repo, filename, markdown, f"Add: {keyword}")
            print(f"    Commit: {'ok' if committed else 'FAILED'}")
            if committed:
                new_urls.append(f"https://{site['domain']}/{keyword_to_slug(keyword)}/")

            # Organic delay between articles
            delay = random.randint(45, 90)
            print(f"    Waiting {delay}s...")
            time.sleep(delay)

        except Exception as e:
            print(f"    Error: {e}")
            continue

    submit_sitemap(site["domain"])
    submit_to_indexing(new_urls)
    print(f"\nDone: {site_name}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--site",  required=True, help="Site name or 'all'")
    parser.add_argument("--count", type=int, default=2, help="Articles per site (default: 2)")
    args = parser.parse_args()

    print(f"Content Publisher | {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}")
    check_api_budget()

    if args.site == "all":
        for site_name in SITES:
            publish_site(site_name, args.count)
    else:
        publish_site(args.site, args.count)

    print("\nPublishing complete.")


if __name__ == "__main__":
    main()
