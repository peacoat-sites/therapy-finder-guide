#!/usr/bin/env python3
"""pipeline/affiliate_links.py
Append Amazon affiliate recommendations to any article in content/posts/
that does not yet have a '## Recommended Resources' section.

Called by publish.yml after publish.py runs and new articles are committed.
Reads SITE_NAME from env to pick the right niche products.
"""
import os, sys, re
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

AMZN_TAG = 'contentportfo-20'

def amz(asin):
    return f'https://www.amazon.com/dp/{asin}/?tag={AMZN_TAG}'

# ── Product catalogue ─────────────────────────────────────────────────────────
PRODUCTS = {
    # Medicare / senior health
    '1119689937': ('Medicare For Dummies',
        'The definitive consumer guide to Medicare — enrollment windows, Part A/B/C/D, and supplement plans.', '~$22'),
    '1668031914': ("Get What's Yours for Medicare",
        'Maximize your Medicare benefits and minimize out-of-pocket costs. Covers Part D drug coverage gaps and Medigap in depth.', '~$17'),
    '1501124005': ("Get What's Yours for Medicare (Original)",
        'The original bestselling guide to navigating Medicare and Social Security timing — over 100,000 copies sold.', '~$15'),

    # Mental health / therapy
    '0380810336': ('Feeling Good: The New Mood Therapy',
        'The most clinically studied self-help book for depression — recommended by therapists worldwide as CBT-based self-treatment.', '~$14'),
    '0452281326': ('The Feeling Good Handbook',
        'Practical workbook companion to Feeling Good — structured CBT exercises for depression, anxiety, and relationship problems.', '~$18'),
    '160623918X': ('The Anxiety and Worry Workbook',
        'Written by Aaron Beck (founder of CBT) — the authoritative structured workbook for managing generalized anxiety disorder.', '~$25'),
    '169327972X': ('Depression & Anxiety Therapy Journal',
        '8-week guided journal with trigger tracking and mood diary — mirrors the homework your therapist would assign between sessions.', '~$10'),
    'B085RPXGM6': ('Coping With Stress: A Therapy Self-Care Journal',
        'Guided self-care journal for managing anxiety and depression — a low-cost tool to complement your therapy work.', '~$10'),
    'B095WS28JL': ('Anti-Anxiety Journal',
        'Daily structured journal for tracking anxiety triggers, patterns, and progress — ideal between therapy sessions.', '~$9'),
    'B09M4THFHN': ('Depression Therapy Journal',
        'Daily check-in journal for depression — structured mood tracking and reflection prompts designed around therapeutic principles.', '~$10'),

    # Solar energy
    'B0B9XB57XM': ('EF EcoFlow DELTA 2 Portable Power Station (1024Wh)',
        '1024Wh LFP battery with 1800W output — top-rated solar generator for home backup power. Charges in under 2 hours.', '~$599'),
    'B0C4DW17PD': ('EF EcoFlow DELTA 2 Max (2048Wh)',
        '2048Wh LFP battery with 2400W output — ideal for whole-home solar backup or pairing with rooftop solar panels.', '~$999'),
    'B00BCRG22A': ('Renogy 200W Solar Starter Kit + 30A Charge Controller',
        'Complete beginner solar kit — 200W monocrystalline panel, charge controller, and mounting hardware included.', '~$169'),
    'B07JXYTFF7': ('Renogy 2×100W Monocrystalline Solar Panels',
        'Expandable 200W panel set from the most trusted DIY solar brand — used widely in off-grid and home backup systems.', '~$99'),
    'B06VYJ8JXH': ('Renogy 200W Solar Kit + 20A MPPT Controller',
        '200W panel kit with MPPT charge controller for maximum energy harvest.', '~$199'),
    'B083KBKJ8Q': ('Jackery Explorer 1000 Portable Power Station (1002Wh)',
        '1002Wh portable power station with three 1000W AC outlets — one of the best-reviewed solar generators on Amazon.', '~$599'),
    'B0D7PPG25F': ('Jackery Explorer 1000 v2 (1070Wh, 1-Hr Charge)',
        'Updated Jackery Explorer 1000 with LFP battery and ultra-fast 1-hour recharge.', '~$799'),
    'B09W21FRBC': ('Renogy 100W Flexible Solar Panel',
        '22% efficiency flexible solar panel that bends up to 240° — designed for curved roofs and irregular mounting surfaces.', '~$89'),

    # Home safety / security
    'B0CX6BWRMM': ('Kidde 10-Year Battery Smoke & CO Detector',
        'Dual smoke and carbon monoxide detector with 10-year sealed battery — no battery replacement needed for a decade.', '~$32'),
    'B086S4Y9H5': ('Kidde Hardwired Smoke & CO Detector w/ Battery Backup',
        'Hardwired interconnected smoke and CO detector — when one alarm sounds, all alarms in the house sound.', '~$40'),
    'B0DDWDDGDS': ('Kidde 10-Year Smoke & CO Detector (4-Pack)',
        'Whole-home 4-pack of 10-year battery-powered detectors — covers a standard 3-bedroom home.', '~$89'),
    'B07K1379PQ': ('Ring Alarm 8-Piece Security Kit',
        'Professional-grade DIY home security system with optional 24/7 monitoring — top way to qualify for insurance discounts.', '~$199'),
    'B08KKNM4LG': ('Ring Alarm 8-Piece Kit + Video Doorbell Bundle',
        'Complete home security kit with video doorbell — documents visitors and can lower home insurance premiums by up to 20%.', '~$299'),

    # Mortgage / home buying
    '0997584785': ('First-Time Home Buyer: The Complete Playbook',
        'The #1 Amazon bestseller in homebuying — covers down payment strategies, mortgage pre-approval, and avoiding rookie mistakes.', '~$18'),
    '1413323456': ("Nolo's Essential Guide to Buying Your First Home",
        'Trusted legal publisher walks you through contracts, disclosures, closing, and every step of homebuying.', '~$25'),
    '1400081971': ('100 Questions Every First-Time Home Buyer Should Ask',
        'Nearly a million copies sold — covers every question to ask your lender, agent, and inspector before signing anything.', '~$17'),
    '1731350120': ('How to Buy Your Perfect First Home',
        'Practical step-by-step guide to qualifying for a mortgage, budgeting correctly, and navigating the full homebuying process.', '~$14'),

    # Personal injury
    'B0DSJS714K': ('Victim to Victory: A Personal Injury Survival Guide',
        'Written by a personal injury attorney — explains the full claims process, how insurance companies calculate settlements.', '~$16'),
    'B0DCV3KHRH': ('Navigating Personal Injury Claims',
        'Covers the pre-litigation claims process step by step — medical documentation, negotiation tactics, and what to expect.', '~$14'),

    # Pet health
    'B0DSKJV741': ('EVERLIT 95-Piece Vet-Approved Pet First Aid Kit',
        'Vet-approved 95-piece kit for dogs and cats — covers cuts, burns, sprains, and emergencies until you can reach a vet.', '~$32'),
    'B07DYSG92T': ('Certified Pet First Aid Kit with Guide Book',
        'Certified pet first aid kit with step-by-step instructions — an essential item for every pet owner.', '~$22'),
    'B003ULL1NQ': ('Nutramax Cosequin DS Joint Supplement for Dogs (132ct)',
        'The #1 veterinarian-recommended joint supplement brand — clinically studied for reducing joint pain in dogs.', '~$36'),
    'B00028ZLTU': ('Nutramax Cosequin + MSM Chewable Tablets (250ct)',
        'Large 250-count supply of vet-recommended Cosequin with MSM for enhanced joint support.', '~$52'),
    'B07218JGWH': ('Nutramax Cosequin Senior Dog Soft Chews (60ct)',
        'Senior-specific Cosequin formula with added Omega-3s — designed for aging dogs with joint and immune health needs.', '~$32'),
    'B00XEVJB84': ('Purina Pro Plan FortiFlora Probiotic for Dogs (30ct)',
        'The #1 vet-recommended probiotic for dogs — prescribed to manage diarrhea, vomiting, and intestinal upset.', '~$32'),
    'B001O3UE9E': ('Nutramax Proviable Probiotics for Dogs & Cats (80ct)',
        'Multi-strain probiotic for both dogs and cats — supports digestive health and immune function.', '~$32'),
    'B01N0BZUXO': ('PetArmor Plus Flea & Tick Prevention — Small Dogs (6 doses)',
        'Same active ingredient as Frontline Plus at a lower price — waterproof topical flea and tick prevention.', '~$32'),
    'B01N03Q8Q1': ('PetArmor Plus Flea & Tick Prevention — Medium Dogs (6 doses)',
        'Vet-quality flea and tick prevention for dogs 23–44 lbs at a fraction of the prescription price — 6-month supply.', '~$32'),
    'B00T72ST0A': ('Adventure Medical Me & My Dog First Aid Kit',
        'Dual human and canine first aid kit — covers injuries for both you and your dog during hikes and travel.', '~$32'),

    # Small business finance
    '1836649975': ('Mastering QuickBooks 2025',
        'The most comprehensive QuickBooks 2025 guide — covers bookkeeping, payroll, invoicing, tax prep, and cash flow.', '~$32'),
    '0692957790': ('QuickBooks Small Business Bookkeeping Guide',
        'Compact, practical QuickBooks pocket guide — ideal for new business owners setting up accounting for the first time.', '~$17'),
    '1623155363': ('Accounting for Small Business Owners',
        'Beginner-friendly accounting guide covering basic bookkeeping, financial statements, and managing business taxes.', '~$14'),
}

DISCLOSURE = (
    "\n> **Disclosure:** *As an Amazon Associate, we earn a small commission from qualifying "
    "purchases at no extra cost to you. We only recommend products that genuinely support the "
    "topics covered in this article.*\n"
)

SECTION_MARKER = '## Recommended Resources'

# ── Niche base products (shown on every article in each site) ─────────────────
NICHE_BASE = {
    'medicare-starter':        ['1119689937', '1668031914'],
    'solar-planner-guide':     ['B00BCRG22A', 'B0B9XB57XM'],
    'solar-home-planner':      ['B00BCRG22A', 'B07JXYTFF7'],
    'injury-victim-guide':     ['B0DSJS714K', 'B0DCV3KHRH'],
    'home-insurance-guide':    ['B0CX6BWRMM', 'B07K1379PQ'],
    'mortgage-advisor-guide':  ['0997584785', '1400081971'],
    'therapy-finder-guide':    ['0380810336', '169327972X'],
    'pet-doctor-guide':        ['B0DSKJV741', 'B003ULL1NQ'],
    'small-biz-finance-guide': ['1836649975', '1623155363'],
}

# ── Keyword extras (matched against lowercased article title) ─────────────────
KEYWORD_EXTRAS = {
    # Medicare
    'part d':            ['1668031914', '1501124005'],
    'drug':              ['1668031914'],
    'medigap':           ['1119689937', '1501124005'],
    'supplement':        ['1119689937', '1668031914'],
    'advantage':         ['1119689937', '1668031914'],
    'coverage':          ['1119689937'],
    'mental health':     ['0380810336'],
    # Solar
    'battery':           ['B0B9XB57XM', 'B0C4DW17PD'],
    'storage':           ['B0B9XB57XM', 'B0C4DW17PD'],
    'generator':         ['B083KBKJ8Q', 'B0D7PPG25F'],
    'cost':              ['B00BCRG22A', 'B07JXYTFF7'],
    'roi':               ['B00BCRG22A', 'B0B9XB57XM'],
    'savings':           ['B00BCRG22A', 'B07JXYTFF7'],
    'payback':           ['B00BCRG22A', 'B07JXYTFF7'],
    'diy':               ['B00BCRG22A', 'B06VYJ8JXH'],
    'panels':            ['B07JXYTFF7', 'B00BCRG22A'],
    'flat roof':         ['B09W21FRBC', 'B00BCRG22A'],
    'flexible':          ['B09W21FRBC'],
    'mobile home':       ['B00BCRG22A', 'B083KBKJ8Q'],
    'tax credit':        ['B0B9XB57XM', 'B00BCRG22A'],
    'net metering':      ['B0B9XB57XM', 'B07JXYTFF7'],
    'installer':         ['B00BCRG22A'],
    'company':           ['B00BCRG22A'],
    # Injury
    'document':          ['B0DCV3KHRH'],
    'settlement':        ['B0DSJS714K', 'B0DCV3KHRH'],
    'accident':          ['B0DSJS714K'],
    'whiplash':          ['B0DSJS714K'],
    'back injury':       ['B0DSJS714K'],
    'brain injury':      ['B0DSJS714K'],
    'broken bone':       ['B0DSJS714K'],
    'soft tissue':       ['B0DSJS714K'],
    'pain and suffering': ['B0DSJS714K'],
    'lawsuit':           ['B0DSJS714K', 'B0DCV3KHRH'],
    # Home insurance
    'smoke':             ['B0CX6BWRMM', 'B086S4Y9H5'],
    'safety':            ['B0CX6BWRMM', 'B0DDWDDGDS'],
    'security':          ['B07K1379PQ', 'B08KKNM4LG'],
    'lower':             ['B0CX6BWRMM', 'B07K1379PQ'],
    'disaster':          ['B0B9XB57XM', 'B0CX6BWRMM'],
    'fire':              ['B0CX6BWRMM', 'B086S4Y9H5'],
    'first time buyer':  ['B0CX6BWRMM', 'B07K1379PQ'],
    'renters':           ['B0CX6BWRMM'],
    'flood':             ['B0B9XB57XM'],
    'deductible':        ['B0DDWDDGDS'],
    'quotes':            ['B07K1379PQ'],
    'cheap':             ['B0DDWDDGDS'],
    'liability':         ['B08KKNM4LG'],
    'dwelling':          ['B0CX6BWRMM', 'B086S4Y9H5'],
    # Mortgage
    'first time':        ['0997584785', '1413323456'],
    'fha':               ['1413323456', '0997584785'],
    'va loan':           ['1731350120', '0997584785'],
    'usda':              ['1731350120'],
    'refinance':         ['0997584785'],
    'pre-approval':      ['0997584785', '1400081971'],
    'down payment':      ['0997584785', '1400081971'],
    'afford':            ['0997584785', '1400081971'],
    'pmi':               ['0997584785'],
    'conventional':      ['1413323456'],
    'qualify':           ['0997584785'],
    'rate':              ['0997584785'],
    '30 year':           ['0997584785', '1400081971'],
    '15 year':           ['0997584785'],
    # Therapy
    'cbt':               ['0380810336', '0452281326'],
    'cognitive':         ['0380810336', '160623918X'],
    'anxiety':           ['160623918X', 'B085RPXGM6'],
    'depression':        ['0380810336', 'B09M4THFHN'],
    'dbt':               ['0452281326', 'B085RPXGM6'],
    'emdr':              ['169327972X'],
    'couples':           ['0452281326'],
    'child therapy':     ['0380810336'],
    'teen':              ['0380810336', 'B095WS28JL'],
    'online therapy':    ['0380810336', 'B085RPXGM6'],
    'find a therapist':  ['0380810336', 'B085RPXGM6'],
    'insurance cover':   ['B085RPXGM6'],
    'first session':     ['169327972X', 'B085RPXGM6'],
    'types of':          ['0380810336', '160623918X'],
    'family therapy':    ['0452281326'],
    # Pet
    'first vet':         ['B0DSKJV741', 'B07DYSG92T'],
    'puppy':             ['B07DYSG92T', 'B00XEVJB84'],
    'kitten':            ['B07DYSG92T', 'B001O3UE9E'],
    'flea':              ['B01N0BZUXO', 'B01N03Q8Q1'],
    'tick':              ['B01N0BZUXO', 'B01N03Q8Q1'],
    'heartworm':         ['B01N0BZUXO', 'B01N03Q8Q1'],
    'dental':            ['B003ULL1NQ', 'B00XEVJB84'],
    'diarrhea':          ['B00XEVJB84', 'B001O3UE9E'],
    'vomit':             ['B00XEVJB84', 'B001O3UE9E'],
    'limping':           ['B003ULL1NQ', 'B0DSKJV741'],
    'joint':             ['B003ULL1NQ', 'B07218JGWH'],
    'senior':            ['B07218JGWH', 'B003ULL1NQ'],
    'not eating':        ['B00XEVJB84', 'B001O3UE9E'],
    'hiding':            ['B001O3UE9E'],
    'litter box':        ['B001O3UE9E'],
    'teeth':             ['B003ULL1NQ'],
    'vaccin':            ['B07DYSG92T'],
    'insurance':         ['B07DYSG92T', 'B0DSKJV741'],
    'annual':            ['B07DYSG92T'],
    'how often':         ['B07DYSG92T'],
    # Small biz
    'bookkeeping':       ['1836649975', '0692957790'],
    'quickbooks':        ['1836649975'],
    'accounting':        ['1836649975', '1623155363'],
    'loan':              ['1836649975', '1623155363'],
    'sba':               ['1836649975'],
    'grant':             ['1623155363'],
    'llc':               ['1623155363', '0692957790'],
    'tax':               ['1836649975', '1623155363'],
    'invoice':           ['1836649975'],
    'bank account':      ['1836649975'],
    'credit score':      ['1836649975'],
    'cash flow':         ['1836649975', '0692957790'],
    'separate':          ['1836649975', '0692957790'],
    'payroll':           ['1836649975'],
    'receivable':        ['1836649975'],
}


def build_section(asins: list) -> str:
    lines = ["\n---\n\n## Recommended Resources\n", DISCLOSURE, "\n"]
    for asin in asins:
        if asin not in PRODUCTS:
            continue
        name, desc, price = PRODUCTS[asin]
        lines.append(f"- **[{name}]({amz(asin)})** ({price}) — {desc}\n")
    return "".join(lines)


def pick_asins(slug: str, title: str) -> list:
    title_l = title.lower()
    base = list(NICHE_BASE.get(slug, []))
    extras = []
    for kw, asins in KEYWORD_EXTRAS.items():
        if kw in title_l:
            for a in asins:
                if a not in base and a not in extras:
                    extras.append(a)
    combined = base[:]
    for a in extras:
        if a not in combined:
            combined.append(a)
        if len(combined) >= 4:
            break
    return combined


def extract_title(content: str) -> str:
    m = re.search(r'^title\s*[=:]\s*["\']?(.+?)["\']?\s*$', content, re.MULTILINE)
    return m.group(1).strip().strip('"\'') if m else ''


def main():
    slug = os.environ.get('SITE_NAME', '').strip()
    if not slug:
        print("ERROR: SITE_NAME environment variable not set", file=sys.stderr)
        sys.exit(1)

    posts_dir = Path('content/posts')
    if not posts_dir.exists():
        print("No content/posts/ directory — nothing to do.")
        sys.exit(0)

    md_files = sorted(posts_dir.glob('*.md'))
    if not md_files:
        print("No markdown files in content/posts/ — nothing to do.")
        sys.exit(0)

    updated = 0
    skipped = 0

    for path in md_files:
        content = path.read_text(encoding='utf-8', errors='replace')

        if SECTION_MARKER in content:
            skipped += 1
            continue

        title = extract_title(content) or path.stem.replace('-', ' ')
        asins = pick_asins(slug, title)

        if not asins:
            skipped += 1
            continue

        section = build_section(asins)
        new_content = content.rstrip() + "\n" + section + "\n"
        path.write_text(new_content, encoding='utf-8')

        names = [PRODUCTS[a][0][:22] for a in asins if a in PRODUCTS]
        print(f"  + {title[:52]:<52} → {', '.join(names)}")
        updated += 1

    print(f"\nAffiliate links: {updated} updated, {skipped} skipped")


if __name__ == '__main__':
    main()
