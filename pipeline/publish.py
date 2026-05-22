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
        "authors": [
            {"name": "James Harmon", "bio": "Former insurance adjuster with 15 years in claims. Now a consumer advocate helping injury victims understand the process."},
            {"name": "Sarah Chen", "bio": "Paralegal specializing in personal injury. Helped process over 400 claims across 3 law firms."},
            {"name": "Michael Torres", "bio": "Legal educator and personal injury claims consultant. Writes to help everyday people navigate the system."},
            {"name": "Rachel Webb", "bio": "Consumer rights advocate with a background in insurance claims and settlement negotiation."},
        ],
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
        "authors": [
            {"name": "Linda Morrison", "bio": "Certified Medicare counselor with 20 years helping seniors understand their benefits options."},
            {"name": "Robert Hughes", "bio": "Retired insurance broker who specialized in Medicare supplements for over 18 years."},
            {"name": "Carol Davenport", "bio": "Senior health advocate and Medicare education specialist. Volunteers with State Health Insurance Assistance Programs (SHIP)."},
            {"name": "David Park", "bio": "Benefits counselor focused on helping Medicare-eligible Americans understand their coverage options."},
        ],
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
        "authors": [
            {"name": "Derek Walsh", "bio": "Solar installation project manager with 10+ years in residential solar across 6 states."},
            {"name": "Amanda Park", "bio": "Home energy analyst and solar consultant. Specializes in helping homeowners evaluate solar ROI."},
            {"name": "Chris Navarro", "bio": "Certified energy auditor and NABCEP-trained solar advisor. Has evaluated over 500 home solar proposals."},
            {"name": "Priya Mehta", "bio": "Renewable energy researcher and homeowner solar advocate. Focuses on financing, incentives, and long-term savings."},
        ],
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
        "authors": [
            {"name": "Tom Briggs", "bio": "Licensed electrician and solar installer with 12 years experience in DIY and residential solar projects."},
            {"name": "Jennifer Liu", "bio": "Green building specialist and NABCEP certified solar professional. Focuses on permits, codes, and contractor selection."},
            {"name": "Marcus Reed", "bio": "Off-grid systems designer and solar workshop instructor. Helps homeowners plan systems from sizing to battery backup."},
            {"name": "Kevin Walsh", "bio": "Residential solar contractor turned educator. Writes to help homeowners avoid common installation pitfalls."},
        ],
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
        "authors": [
            {"name": "Patricia Lawson", "bio": "Former claims adjuster with 14 years at a national insurer. Now helps homeowners understand their coverage before they need it."},
            {"name": "Gary Simmons", "bio": "Independent insurance agent with 20 years helping homeowners compare policies and understand coverage limits."},
            {"name": "Diane Kowalski", "bio": "Consumer insurance educator and public adjuster with experience handling hundreds of homeowner claims."},
            {"name": "Brian Oates", "bio": "Risk assessment specialist and home insurance consultant. Focuses on helping homeowners avoid underinsurance."},
        ],
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
        "authors": [
            {"name": "David Carver", "bio": "Former mortgage underwriter with 16 years at regional and national lenders. Now explains the mortgage process to home buyers."},
            {"name": "Susan Park", "bio": "HUD-approved housing counselor and mortgage educator. Has guided over 500 first-time buyers through the loan process."},
            {"name": "Tom Whitfield", "bio": "Mortgage broker turned consumer advocate. Helps borrowers compare loan products and avoid costly mistakes."},
            {"name": "Alicia Rivera", "bio": "Real estate finance educator specializing in first-time homebuyer programs and down payment assistance."},
        ],
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
        "authors": [
            {"name": "Maya Okonkwo", "bio": "Mental health educator and therapist referral specialist. Has helped hundreds of people find their first or next therapist."},
            {"name": "Jordan Hayes", "bio": "Licensed clinical social worker and mental health blogger. Writes to reduce stigma and help people access care."},
            {"name": "Dr. Emily Strauss", "bio": "Psychologist and mental health advocate with 12 years in community mental health and private practice settings."},
            {"name": "Carlos Mendez", "bio": "Peer support specialist and mental health recovery educator. Writes from lived experience and professional training."},
        ],
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
        "authors": [
            {"name": "Karen Briggs", "bio": "Registered veterinary technician with 13 years of clinical experience in small animal care."},
            {"name": "Dr. Samuel Osei", "bio": "Veterinarian with 10 years in general practice and emergency medicine. Writes to help owners recognize warning signs early."},
            {"name": "Lisa Tanaka", "bio": "Certified veterinary nurse and pet nutrition specialist. Focuses on preventive care and wellness."},
            {"name": "Mark Callahan", "bio": "Animal behaviorist and veterinary educator. Helps owners understand their pets' health and behavior."},
        ],
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
        "authors": [
            {"name": "Frank Medina", "bio": "Small business CFO and financial consultant with 18 years helping entrepreneurs set up clean financial systems. Former CPA."},
            {"name": "Angela Torres", "bio": "Small business development advisor and former SBDC counselor. Has helped 300+ businesses with financial planning."},
            {"name": "James O'Brien", "bio": "Enrolled agent and small business tax specialist. Focuses on tax planning and bookkeeping for self-employed and small businesses."},
            {"name": "Priya Nair", "bio": "Business finance educator and startup CFO. Specializes in cash flow management and small business funding strategy."},
        ],
        "ymyl": True,
        "disclaimer": (
            "*This article is for general informational purposes only and does not constitute financial, tax, "
            "or legal advice. Business finance and tax rules vary by entity type, state, and individual "
            "circumstances. Consult a qualified CPA, enrolled agent, or business attorney for advice "
            "specific to your situation.*"
        ),
    },
}

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

STOP_WORDS = {
    'how','does','what','is','are','to','the','a','an','for','of','with',
    'in','on','at','by','from','vs','and','or','not','do','can','will',
    'your','you','best','top','guide','tips','explained','complete','why',
    'when','where','which','who','should','would','could','get','make',
    'using','use','about','into','more','most','all','any','its','this'
}

def title_to_image_query(title: str, fallback_query: str) -> str:
    """Generate a specific Pexels search query from an article title."""
    import re
    words = re.sub(r'[^a-zA-Z0-9\s]', '', title.lower()).split()
    keywords = [w for w in words if w not in STOP_WORDS and len(w) > 2]
    if len(keywords) >= 2:
        return ' '.join(keywords[:4])
    return fallback_query

# ── IMAGE FETCHING ────────────────────────────────────────────────────────────

def fetch_image_pexels(query: str, used_ids: set) -> dict | None:
    if not PEXELS_KEY:
        return None
    try:
        r = requests.get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": PEXELS_KEY},
            params={"query": query, "per_page": 15, "orientation": "landscape"},
            timeout=10
        )
        if r.status_code != 200:
            return None
        if int(r.headers.get("X-Ratelimit-Remaining", 200)) < 5:
            return None
        photos = [p for p in r.json().get("photos", []) if str(p["id"]) not in used_ids]
        if not photos:
            return None
        photo = random.choice(photos[:5])
        used_ids.add(str(photo["id"]))
        return {
            "url":          photo["src"]["large"],
            "credit":       photo["photographer"],
            "credit_link":  photo["photographer_url"],
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


def fetch_image(query: str, used_ids: set) -> dict | None:
    img = fetch_image_pexels(query, used_ids)
    if img:
        return img
    print("  Pexels miss -- trying Flux...")
    return fetch_image_flux(query)

# ── PUBLISHED KEYWORD TRACKING ────────────────────────────────────────────────

def get_published_articles(repo: str) -> list:
    """Fetch published article titles and slugs for internal linking."""
    try:
        r = requests.get(
            f"https://api.github.com/repos/{GITHUB_ORG}/{repo}/contents/content/posts",
            headers=GH_HEADERS,
            timeout=15
        )
        if r.status_code == 200:
            articles = []
            for f in r.json():
                if isinstance(f, dict) and f["name"].endswith(".md"):
                    slug = f["name"].replace(".md", "")
                    title = slug.replace("-", " ").title()
                    articles.append({"slug": slug, "title": title, "url": f"/{slug}/"})
            return articles[:20]
    except Exception:
        pass
    return []


def extract_faq_pairs(markdown_text: str) -> list:
    """Extract FAQ question/answer pairs from article markdown."""
    import re
    pairs = []
    # Find FAQ section (## FAQ or ## Frequently Asked Questions)
    faq_match = re.search(r'##\s+(?:FAQ|Frequently Asked Questions|Common Questions)[^\n]*\n(.*?)(?=\n##|\Z)', markdown_text, re.IGNORECASE | re.DOTALL)
    if not faq_match:
        return pairs
    faq_section = faq_match.group(1)
    # Extract H3 question + following paragraph as answer
    questions = re.findall(r'###\s+([^\n]+)\n+(.*?)(?=\n###|\Z)', faq_section, re.DOTALL)
    for question, answer in questions[:8]:
        answer_clean = re.sub(r'\n+', ' ', answer.strip())
        answer_clean = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', answer_clean)  # strip links
        answer_clean = re.sub(r'[*_`]', '', answer_clean)  # strip markdown
        answer_clean = answer_clean[:300].strip()
        if question.strip() and answer_clean:
            pairs.append({"question": question.strip(), "answer": answer_clean})
    return pairs


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

def generate_article(keyword: str, site_config: dict, persona: dict, priority: str) -> dict:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

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

    # Internal linking — pass existing articles so Claude can weave in contextual links
    existing_articles = site_config.get("_published_articles", [])
    internal_link_instruction = ""
    if existing_articles:
        article_list = "\n".join(f'  - [{a["title"]}]({a["url"]})' for a in existing_articles[:15])
        internal_link_instruction = (
            f"- Include 2-3 contextual internal links to related articles from this site. "
            f"Use ONLY URLs from this list, link naturally within sentences, use descriptive anchor text:\n{article_list}"
        )

    ymyl_instruction = ""
    if ymyl:
        ymyl_instruction = (
            "- This is a YMYL (Your Money / Your Life) topic. Be factually accurate, "
            "balanced, and note that professional consultation is always advisable. "
            "Do not make guarantees or quote specific dollar figures as typical outcomes."
        )

    system_prompt = f"""{tone}

You are writing an article for a {niche} website. The author byline will be {persona['name']}.

Length target: {length_target['min']}-{length_target['max']} words ({length_target['label']}).

Writing rules (follow every one):
- Write in a natural, human voice. Vary sentence length -- mix short punchy sentences with longer explanatory ones.
- Never use em dashes (-- or ---). Use commas, colons, or a new sentence instead.
- Avoid these overused AI phrases: "In conclusion", "In summary", "It's worth noting", "Firstly", "Furthermore", "Moreover", "Delve into", "Navigating", "It is important to note", "This article will explore".
- Use contractions naturally (you'll, it's, don't, can't).
- Be specific. Use real numbers, named concepts, and concrete examples instead of vague generalities.
- Occasional first person is fine ("In my experience...", "I've seen clients...") -- it makes the author persona feel real.
- Do not pad with filler. Every paragraph must earn its place.
{ymyl_instruction}
{ref_instruction}
{internal_link_instruction}
{affiliate_note}

Output ONLY the article body in Markdown. No preamble, no title (title comes from frontmatter)."""

    user_prompt = f"""Write a thorough, reader-first article about: {keyword}

Structure:
1. Opening paragraph -- hook the reader with a specific scenario or surprising fact. No heading.
2. 4-6 H2 sections covering the topic comprehensively
3. At least one practical step-by-step section or comparison table where relevant
4. FAQ section (5 Q&A pairs as H3 subheadings)
5. Brief closing paragraph (no heading, no summary label)

Make it genuinely useful for someone dealing with this exact situation right now."""

    msg = client.messages.create(
        model="claude-sonnet-4-6" if priority == "high" else "claude-haiku-4-5",
        max_tokens=4000,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}]
    )
    content = msg.content[0].text

    # Meta description
    meta = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=80,
        messages=[{"role": "user", "content": f"Write a 140-155 character SEO meta description for an article titled '{keyword}'. Plain text only, no quotes."}]
    )
    description = meta.content[0].text.strip()[:160]

    return {"content": content, "description": description}

# ── MARKDOWN BUILDER ──────────────────────────────────────────────────────────

def build_markdown(
    keyword: str,
    article: dict,
    image: dict | None,
    categories: list,
    tags: list,
    persona: dict,
    ymyl: bool,
    disclaimer: str,
    faq_pairs=None,
) -> str:
    slug = keyword_to_slug(keyword)
    date = datetime.now(timezone.utc).isoformat()
    image_url = image["url"] if image else ""

    content = article["content"]

    # Append photo credit
    if image and image.get("credit") and image.get("credit_link"):
        content += f"\n\n*Photo: [{image['credit']}]({image['credit_link']}) via Pexels*"

    # Append YMYL disclaimer
    if ymyl and disclaimer:
        content += f"\n\n---\n\n{disclaimer}"

    faq_yaml = ""
    if faq_pairs:
        faq_lines = "\n".join(
            f'  - q: "{p["question"].replace(chr(34), chr(39))}"\n    a: "{p["answer"].replace(chr(34), chr(39))}"'
            for p in faq_pairs
        )
        faq_yaml = f"faq:\n{faq_lines}\n"

    frontmatter = f"""---
title: "{keyword.title()}"
date: {date}
draft: false
description: "{article['description']}"
image: "{image_url}"
categories: {json.dumps(categories)}
tags: {json.dumps(tags)}
author: "{persona['name']}"
author_bio: "{persona['bio']}"
slug: "{slug}"
affiliate_disclosure: {"true" if AMAZON_TRACKING_ID else "false"}
{faq_yaml}---

"""
    return frontmatter + content

# ── GITHUB COMMIT ─────────────────────────────────────────────────────────────


def load_used_image_ids(repo: str) -> set:
    """Load previously used Pexels image IDs from data/used_images.json."""
    r = requests.get(
        f"https://api.github.com/repos/{GITHUB_ORG}/{repo}/contents/data/used_images.json",
        headers=GH_HEADERS,
    )
    if r.status_code == 200:
        try:
            ids = json.loads(base64.b64decode(r.json()["content"]).decode())
            return set(str(i) for i in ids)
        except Exception:
            pass
    return set()


def save_used_image_ids(repo: str, used_ids: set) -> None:
    """Persist used image IDs back to data/used_images.json so future runs skip them."""
    path = "data/used_images.json"
    payload_content = base64.b64encode(json.dumps(sorted(used_ids)).encode()).decode()
    r = requests.get(
        f"https://api.github.com/repos/{GITHUB_ORG}/{repo}/contents/{path}",
        headers=GH_HEADERS,
    )
    body = {"message": "chore: update used image IDs", "content": payload_content}
    if r.status_code == 200:
        body["sha"] = r.json()["sha"]
    requests.put(
        f"https://api.github.com/repos/{GITHUB_ORG}/{repo}/contents/{path}",
        headers=GH_HEADERS,
        json=body,
    )


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
    order = {"high": 0, "medium": 1, "low": 2}
    rows.sort(key=lambda x: order.get(x.get("priority", "low").lower(), 2))
    return rows

# ── MAIN PUBLISHER ────────────────────────────────────────────────────────────

def publish_site(site_name: str, count: int):
    if site_name not in SITES:
        print(f"Site '{site_name}' not in SITES_CONFIG")
        return

    site = SITES[site_name]
    repo = site["repo"]
    niche = site["niche"]
    print(f"\nPublishing {count} articles to {site_name} ({site['domain']})")

    # Load keywords + already-published slugs + existing articles for internal linking
    keywords          = load_keywords(repo)
    published         = get_published_slugs(repo)
    published_articles = get_published_articles(repo)
    site["_published_articles"] = published_articles
    persona_pool   = SITE_PERSONAS.get(repo, {}).get("authors", [{"name": "Editorial Team", "bio": "Content team."}])
    ymyl           = SITE_PERSONAS.get(repo, {}).get("ymyl", False)
    disclaimer     = SITE_PERSONAS.get(repo, {}).get("disclaimer", "")

    if not keywords:
        print(f"  No keywords found")
        return

    # Skip already-published keywords
    unpublished = [
        kw for kw in keywords
        if keyword_to_slug(kw.get("keyword", "")) not in published
    ]

    if not unpublished:
        print(f"  All keywords already published!")
        return

    to_publish   = unpublished[:count]
    used_img_ids = load_used_image_ids(repo)  # persistent across runs

    for i, kw_row in enumerate(to_publish, 1):
        keyword  = kw_row.get("keyword", "")
        category = kw_row.get("category", niche)
        priority = kw_row.get("priority", "medium").lower()
        persona  = random.choice(persona_pool)

        print(f"\n  [{i}/{count}] {keyword} (priority: {priority}, author: {persona['name']})")

        try:
            check_api_budget()

            # Generate article
            article = generate_article(keyword, site, persona, priority)
            print(f"    Article: {len(article['content'])} chars")

            # Inject affiliate links
            article["content"] = inject_affiliate_links(article["content"], niche)

            # Fetch image
            _title_query = title_to_image_query(keyword, site.get("image_query", keyword))
            image = fetch_image(_title_query, used_img_ids)
            print(f"    Image: {'ok' if image else 'none'}")

            # Extract FAQ pairs for schema
            faq_pairs = extract_faq_pairs(article["content"])

            # Build markdown
            tags = [w for w in keyword.split() if len(w) > 3][:5]
            markdown = build_markdown(
                keyword, article, image,
                categories=[category],
                tags=tags,
                persona=persona,
                ymyl=ymyl,
                disclaimer=disclaimer,
                faq_pairs=faq_pairs,
            )

            # Commit
            filename  = keyword_to_slug(keyword) + ".md"
            committed = commit_to_github(repo, filename, markdown, f"Add: {keyword}")
            print(f"    Commit: {'ok' if committed else 'FAILED'}")

            # Organic delay between articles
            delay = random.randint(45, 90)
            print(f"    Waiting {delay}s...")
            time.sleep(delay)

        except Exception as e:
            print(f"    Error: {e}")
            continue

    save_used_image_ids(repo, used_img_ids)  # persist for next run
    submit_sitemap(site["domain"])
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


