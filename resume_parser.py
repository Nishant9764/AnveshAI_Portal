"""
Resume Parser — Pure Python (regex + heuristics, zero AI API)

Pipeline:
  1. Layout-aware PDF extraction (PyMuPDF, column-detecting) -> falls back to PyPDF2
  2. PII masking (name, email, phone, LinkedIn, GitHub, URLs, address,
     city/state, PIN, DOB, social handles, nationality/gender)
  3. Section splitting (robust header detection, handles "Header: content" lines)
  4. Structured extraction: Skills / Experience / Projects only
"""

import re
import string

try:
    import fitz  # PyMuPDF
    HAS_FITZ = True
except ImportError:
    HAS_FITZ = False

import PyPDF2


# ══════════════════════════════════════════════════════════
#  REFERENCE SETS
# ══════════════════════════════════════════════════════════

KNOWN_LOCATIONS = {
    "mumbai","delhi","bengaluru","bangalore","hyderabad","chennai","kolkata",
    "pune","ahmedabad","jaipur","lucknow","kanpur","nagpur","indore","bhopal",
    "visakhapatnam","vizag","patna","vadodara","ghaziabad","ludhiana","agra",
    "nashik","meerut","faridabad","rajkot","varanasi","srinagar","aurangabad",
    "coimbatore","madurai","mysuru","mysore","thiruvananthapuram","trivandrum",
    "kochi","cochin","chandigarh","guwahati","noida","gurugram","gurgaon",
    "navi mumbai","thane","surat","amritsar","ranchi","raipur","bhubaneswar",
    "maharashtra","karnataka","tamil nadu","telangana","andhra pradesh",
    "uttar pradesh","rajasthan","gujarat","madhya pradesh","west bengal",
    "bihar","odisha","kerala","assam","punjab","haryana","jharkhand",
    "uttarakhand","himachal pradesh","goa","chhattisgarh","manipur",
    "meghalaya","tripura","nagaland","arunachal pradesh","mizoram","sikkim",
    "india","usa","united states","uk","united kingdom","canada","australia",
    "germany","france","singapore","dubai","uae","netherlands","sweden",
    "new york","london","san francisco","seattle","austin","boston","chicago",
    "toronto","sydney","berlin","paris","amsterdam",
}

COMMON_FIRST_NAMES = {
    "aarav","aaditi","aakash","aansh","aaryan","aashish","aayush","abhay",
    "abhi","abhijeet","abhijit","abhilash","abhinav","abhiram","abhishek",
    "aditi","aditya","ajay","ajit","akash","akshay","akshit","alok","amandeep",
    "amarjeet","amit","amitabh","amol","amrit","amruta","anand","aniket",
    "anil","anisha","ankit","ankita","anoop","anupam","anurag","anushka",
    "arjun","arnav","arun","aryan","asha","ashish","ashok","ashutosh",
    "ashu","atharv","avani","ayush","bharat","bhuvan","chirag","deepak",
    "dev","devesh","devansh","dhruv","dinesh","divya","gaurav","gautam",
    "hardik","harsh","harshit","hemant","hitesh","ishan","ishaan","jatin",
    "jay","karan","kartik","kavya","krish","krishna","kunal","lalit",
    "lokesh","mahesh","manish","manvir","mayank","mihir","milan","mohit",
    "mukesh","nakul","naman","naveen","nikhil","nilesh","nitin","priya",
    "om","paras","pavan","piyush","pooja","pradeep","prakash","pranav",
    "prashant","pratik","priyansh","rahul","raj","rajat","rajesh",
    "rakesh","ravi","ritesh","rohan","rohit","sachin","sahil","sajan",
    "sandeep","sanjay","sanket","saurabh","shashi","shivam","shubham",
    "shreya","siddharth","sneha","soham","sourabh","subham","sudhir",
    "sumit","suresh","suyash","swati","tanuj","tanvi","tarun","tushar",
    "uday","umesh","utkarsh","vaibhav","vikas","vikram","vipin",
    "vishal","vivek","yash","yogesh","zoya",
    "aaron","adam","alex","alice","andrew","anna","ashley","ben","brian",
    "charles","chris","daniel","david","emily","emma","ethan","george",
    "henry","jack","james","jessica","john","joshua","julia","kevin",
    "laura","liam","lily","lisa","lucas","mark","matthew","michael",
    "noah","olivia","ryan","sarah","sofia","thomas","william",
}

JOB_TITLE_WORDS = {
    "engineer","developer","analyst","manager","designer","architect","intern",
    "consultant","specialist","lead","senior","junior","associate","director",
    "officer","executive","coordinator","administrator","scientist","researcher",
    "student","fresher","graduate","undergraduate","postgraduate","full","stack",
    "backend","frontend","software","data","machine","learning","cloud","devops",
    "qa","tester","product","project","program","technical","technology",
}

SECTION_KEYWORDS = {
    "skills": "skills","technical skills": "skills","core skills": "skills",
    "key skills": "skills","competencies": "skills","core competencies": "skills",
    "technologies": "skills","tech stack": "skills","tools": "skills",
    "tools & technologies": "skills","tools and technologies": "skills",
    "programming languages": "skills","expertise": "skills",
    "proficiencies": "skills","skill set": "skills","skillset": "skills",
    "experience": "experience","work experience": "experience",
    "professional experience": "experience","employment history": "experience",
    "work history": "experience","internships": "experience","internship": "experience",
    "industry experience": "experience","career history": "experience",
    "positions held": "experience","relevant experience": "experience",
    "projects": "projects","project work": "projects","academic projects": "projects",
    "personal projects": "projects","key projects": "projects",
    "notable projects": "projects","project experience": "projects","portfolio": "projects",
    "education": "education","academic background": "education",
    "qualifications": "education","certifications": "certifications",
    "certificates": "certifications","achievements": "achievements",
    "awards": "achievements","honours": "achievements","honors": "achievements",
    "summary": "summary","objective": "summary","profile": "summary","about": "summary",
    "declaration": "declaration","references": "references",
    "hobbies": "hobbies","interests": "hobbies","extra-curricular": "hobbies",
    "extracurricular": "hobbies","languages known": "other",
}

# Sub-category labels seen inside Skills sections (e.g. "Frontend: HTML, CSS")
SKILL_CATEGORY_LABELS = {
    "frontend","front-end","front end","backend","back-end","back end",
    "languages","programming languages","frameworks","framework",
    "libraries","library","databases","database","tools","tool",
    "platforms","platform","cloud","devops","testing","version control",
    "methodologies","soft skills","technical skills","other skills",
    "concepts","operating systems","os","design","ai/ml","ml","ai",
    "data science","mobile","web","scripting","markup","query languages",
    "other","misc","miscellaneous","ides","ide",
}

# Verbs that signal a line is a sentence/bullet (experience leak), not a skill or title
ACTION_VERBS = {
    "developed","built","designed","implemented","managed","created","led",
    "collaborated","worked","responsible","spearheaded","architected",
    "coordinated","conducted","analyzed","tested","maintained","deployed",
    "automated","streamlined","optimized","achieved","improved","reduced",
    "increased","assisted","contributed","participated","performed",
    "executed","delivered","established","initiated","organized","oversaw",
    "supervised","trained","mentored","resolved","handled","integrated",
    "configured","monitored","documented","presented","reviewed","wrote",
    "authored","launched","migrated","refactored","debugged","engineered",
}

# Labels that mark metadata lines inside a Projects entry
PROJECT_METADATA_LABELS_RE = re.compile(
    r'^(tech\s*stack|technolog(?:y|ies)|tools?\s*used|tools?|built\s*with|'
    r'stack|languages?\s*used|frameworks?\s*used|github|link|demo|'
    r'live\s*(?:link|demo)|repository|repo|url|duration|role)$',
    re.IGNORECASE
)

TECH_WORDS = {
    "python","java","javascript","typescript","c++","c#","c","go","rust","ruby",
    "php","swift","kotlin","scala","r","matlab","perl","bash","shell","sql","html",
    "css","xml","json","yaml","react","angular","vue","nextjs","gatsby",
    "express","django","flask","fastapi","spring","springboot","rails","laravel",
    "node","nodejs","tensorflow","pytorch","keras","scikit-learn","opencv",
    "pandas","numpy","matplotlib","seaborn","plotly","scipy","nltk","spacy","bert",
    "gpt","llm","transformer","mysql","postgresql","postgres","mongodb","redis",
    "sqlite","oracle","firebase","dynamodb","cassandra","elasticsearch","graphql",
    "rest","grpc","docker","kubernetes","k8s","aws","gcp","azure","heroku",
    "vercel","netlify","linux","git","nginx","apache","kafka","rabbitmq",
    "celery","jwt","oauth","android","ios","flutter","react native","unity",
    "opengl","cuda","spark","hadoop","airflow","tableau","powerbi","streamlit",
    "gradio","langchain","pinecone","faiss","html5","css3","sass","tailwind",
    "bootstrap","webpack","vite","jest","pytest","selenium","cypress","postman",
}


# ══════════════════════════════════════════════════════════
#  PDF EXTRACTION — layout-aware, column-detecting (PyMuPDF)
# ══════════════════════════════════════════════════════════

def _extract_lines_with_bbox(page):
    """Group words into visual lines using (block_no, line_no) with bbox."""
    words = page.get_text("words")
    line_groups = {}
    for x0, y0, x1, y1, word, block_no, line_no, word_no in words:
        key = (block_no, line_no)
        g = line_groups.setdefault(key, {"words": [], "x0": x0, "y0": y0, "x1": x1, "y1": y1})
        g["words"].append((word_no, word))
        g["x0"] = min(g["x0"], x0); g["x1"] = max(g["x1"], x1)
        g["y0"] = min(g["y0"], y0); g["y1"] = max(g["y1"], y1)

    lines = []
    for g in line_groups.values():
        text = " ".join(w for _, w in sorted(g["words"], key=lambda t: t[0]))
        lines.append({"text": text, "x0": g["x0"], "x1": g["x1"], "y0": g["y0"], "y1": g["y1"]})
    return lines


def _detect_column_split(lines, page_width):
    """Return an x-coordinate splitting the page into two columns, or None."""
    if len(lines) < 6:
        return None
    xs = sorted(set(round(l["x0"] / 5) * 5 for l in lines))
    best_gap, best_split = 0, None
    lo, hi = page_width * 0.15, page_width * 0.85
    for i in range(len(xs) - 1):
        gap = xs[i + 1] - xs[i]
        mid = (xs[i] + xs[i + 1]) / 2
        if lo < mid < hi and gap > best_gap:
            best_gap, best_split = gap, mid
    if best_gap < 30:
        return None
    left_count = sum(1 for l in lines if l["x0"] < best_split)
    right_count = sum(1 for l in lines if l["x0"] >= best_split)
    total = len(lines)
    if left_count < total * 0.15 or right_count < total * 0.15:
        return None
    return best_split


def _find_column_start_y(left_lines, right_lines, tol=15):
    """Find the y where both columns first run in parallel (closely-aligned rows)."""
    best = None
    for l in left_lines:
        for r in right_lines:
            if abs(l["y0"] - r["y0"]) <= tol:
                y = min(l["y0"], r["y0"])
                if best is None or y < best:
                    best = y
    return best


def _reorder_two_column(lines, split_x):
    left_zone = [l for l in lines if l["x0"] < split_x]
    right_zone = [l for l in lines if l["x0"] >= split_x]
    if not left_zone or not right_zone:
        return None

    col_start_y = _find_column_start_y(left_zone, right_zone)
    if col_start_y is None:
        col_start_y = min(min(l["y0"] for l in left_zone), min(l["y0"] for l in right_zone))

    header = sorted([l for l in lines if l["y0"] < col_start_y], key=lambda l: l["y0"])
    left = sorted([l for l in left_zone if l["y0"] >= col_start_y], key=lambda l: l["y0"])
    right = sorted([l for l in right_zone if l["y0"] >= col_start_y], key=lambda l: l["y0"])

    ordered = header + left + right
    return "\n".join(l["text"] for l in ordered)


def _extract_page_smart(page):
    lines = _extract_lines_with_bbox(page)
    if not lines:
        return page.get_text()
    split_x = _detect_column_split(lines, page.rect.width)
    if split_x is None:
        lines.sort(key=lambda l: (round(l["y0"]), l["x0"]))
        return "\n".join(l["text"] for l in lines)
    result = _reorder_two_column(lines, split_x)
    return result if result else page.get_text()


def extract_text_from_pdf(file):
    """Extract resume text, using layout-aware column detection when possible."""
    if HAS_FITZ:
        try:
            file.seek(0)
            data = file.read()
            doc = fitz.open(stream=data, filetype="pdf")
            pages_text = [_extract_page_smart(page) for page in doc]
            doc.close()
            text = "\n\n".join(pages_text)
            if text.strip():
                return text
        except Exception:
            pass  # fall through to PyPDF2

    # Fallback: PyPDF2 (simple, no column awareness)
    file.seek(0)
    reader = PyPDF2.PdfReader(file)
    text = ""
    for page in reader.pages:
        extracted = page.extract_text()
        if extracted:
            text += extracted + "\n"
    return text


# ══════════════════════════════════════════════════════════
#  SECTION DETECTION
# ══════════════════════════════════════════════════════════

def detect_section_and_remainder(line):
    """Return (canonical_section, remainder_text) if line is a header, else (None, None)."""
    stripped = line.strip()
    if not stripped:
        return None, None
    cleaned = re.sub(r'^[\u2022\u25aa\u25cf\u25a0\u2756\-\*\s]+', '', stripped)

    bare = cleaned.rstrip(':-\u2013\u2014 \t').strip()
    norm = re.sub(r'\s+', ' ', bare).lower()
    if norm in SECTION_KEYWORDS and len(bare) <= 40:
        return SECTION_KEYWORDS[norm], None

    m = re.match(r'^([A-Za-z][A-Za-z &/]{1,30}?)\s*[:\-\u2013\u2014]\s*(.+)$', cleaned)
    if m:
        head_norm = re.sub(r'\s+', ' ', m.group(1)).strip().lower()
        if head_norm in SECTION_KEYWORDS:
            return SECTION_KEYWORDS[head_norm], m.group(2).strip()

    return None, None


def split_into_sections(lines):
    sections = {"header": []}
    current_section = "header"
    for line in lines:
        sec, remainder = detect_section_and_remainder(line)
        if sec:
            current_section = sec
            sections.setdefault(current_section, [])
            if remainder:
                sections[current_section].append(remainder)
        else:
            sections.setdefault(current_section, []).append(line)
    return sections


# ══════════════════════════════════════════════════════════
#  NAME DETECTION (heuristic)
# ══════════════════════════════════════════════════════════

def looks_like_name(line):
    stripped = line.strip()
    if not stripped or len(stripped) < 3 or len(stripped) > 60:
        return False
    if not re.match(r"^[A-Za-z][A-Za-z\s.\-']{2,59}$", stripped):
        return False
    words = stripped.split()
    if not (2 <= len(words) <= 5):
        return False
    if not all(w[0].isupper() for w in words if len(w) > 1):
        return False
    lower_words = {w.lower() for w in words}
    if lower_words & JOB_TITLE_WORDS:
        return False
    full_lower = stripped.lower()
    if any(full_lower == kw or full_lower.startswith(kw) for kw in SECTION_KEYWORDS):
        return False
    return True


def find_candidate_name(lines):
    checked = 0
    for line in lines:
        s = line.strip()
        if not s:
            continue
        if checked >= 8:
            break
        checked += 1
        if looks_like_name(s):
            return s
    return None


# ══════════════════════════════════════════════════════════
#  REGEX PII MASKER
# ══════════════════════════════════════════════════════════

def mask_pii(text, candidate_name=None):

    if candidate_name:
        escaped = re.escape(candidate_name)
        text = re.sub(r'(?i)\b' + escaped + r'\b', '[NAME REDACTED]', text)
        for part in candidate_name.split():
            if len(part) >= 4:
                text = re.sub(r'(?i)\b' + re.escape(part) + r'\b', '[NAME REDACTED]', text)

    text = re.sub(
        r'[a-zA-Z0-9_.+\-]+@[a-zA-Z0-9\-]+\.[a-zA-Z0-9\-.]+',
        '[EMAIL REDACTED]', text
    )

    def phone_replacer(m):
        digits = re.sub(r'\D', '', m.group())
        return '[PHONE REDACTED]' if len(digits) >= 7 else m.group()

    text = re.sub(
        r'(\+?\d{1,3}[\s.\-]?)?(\(?\d{2,5}\)?[\s.\-]?)(\d{3,5}[\s.\-]?\d{3,5})',
        phone_replacer, text
    )

    text = re.sub(
        r'(https?://)?(www\.)?linkedin\.com/(in|pub|profile|company)/[\w\-%.]+/?(\?[\w=&%\-]*)?',
        '[LINKEDIN REDACTED]', text, flags=re.IGNORECASE
    )
    text = re.sub(
        r'(https?://)?(www\.)?github\.com/[\w\-]+(/[\w\-\.]*)*/?',
        '[GITHUB REDACTED]', text, flags=re.IGNORECASE
    )
    text = re.sub(
        r'(https?://)?(www\.)?(twitter|x)\.com/[\w]+',
        '[TWITTER REDACTED]', text, flags=re.IGNORECASE
    )
    text = re.sub(r'@[\w]{3,}', '[HANDLE REDACTED]', text)
    text = re.sub(r'https?://[^\s\)\],\'"<>]+', '[URL REDACTED]', text)
    text = re.sub(r'\bwww\.[^\s\)\],\'"<>]+', '[URL REDACTED]', text)
    text = re.sub(
        r'\b[a-zA-Z0-9\-]{3,30}\.(dev|me|site|tech|xyz|app)\b(?!/)',
        '[DOMAIN REDACTED]', text, flags=re.IGNORECASE
    )

    text = re.sub(
        r'\b\d{1,5}\s+[A-Za-z\s]{2,30}(Street|St\.?|Avenue|Ave\.?|Road|Rd\.?|'
        r'Boulevard|Blvd\.?|Lane|Ln\.?|Drive|Dr\.?|Court|Ct\.?|Place|Pl\.?|'
        r'Nagar|Colony|Layout|Sector|Block|Phase|Apartment|Apt\.?|Flat|Floor|'
        r'Cross|Main|Circle|Square|Park|Garden|Marg|Vihar|Enclave|Extension)\b',
        '[ADDRESS REDACTED]', text, flags=re.IGNORECASE
    )

    text = re.sub(
        r'(?<!\d)(?!19\d\d|20[012]\d)\d{6}(?!\d)',
        '[PIN REDACTED]', text
    )

    text = re.sub(r'\b\d{4}[\s\-]\d{4}[\s\-]\d{4}\b', '[ID REDACTED]', text)

    text = re.sub(
        r'(D\.?O\.?B\.?|Date\s+of\s+Birth|Born\s*:?|DOB\s*:?)\s*'
        r'[\d]{1,2}[\/\-\.][\d]{1,2}[\/\-\.][\d]{2,4}',
        '[DOB REDACTED]', text, flags=re.IGNORECASE
    )
    text = re.sub(
        r'(D\.?O\.?B\.?|Date\s+of\s+Birth|Born)\s*:?\s*'
        r'(January|February|March|April|May|June|July|August|September|October|November|December)'
        r'\s+\d{1,2},?\s+\d{4}',
        '[DOB REDACTED]', text, flags=re.IGNORECASE
    )

    text = re.sub(
        r'(Nationality|Gender|Sex|Religion|Marital[ ]Status|Caste)\s*:\s*[A-Za-z][A-Za-z /]{1,29}(?=[\|\n,\r]|$)',
        lambda m: m.group(0).split(':')[0] + ': [PERSONAL INFO REDACTED]',
        text, flags=re.IGNORECASE | re.MULTILINE
    )

    lines_out = []
    for line in text.splitlines():
        parts = [p.strip() for p in re.split(r'[,|/]+', line.strip()) if p.strip()]
        if parts and all(p.lower() in KNOWN_LOCATIONS for p in parts if len(p.strip()) > 1):
            lines_out.append('[LOCATION REDACTED]')
        else:
            line = re.sub(
                r'\b(' + '|'.join(re.escape(loc) for loc in KNOWN_LOCATIONS) + r')\b',
                '[LOCATION REDACTED]', line, flags=re.IGNORECASE
            )
            lines_out.append(line)
    text = '\n'.join(lines_out)

    return text


# ══════════════════════════════════════════════════════════
#  SKILLS PARSER
# ══════════════════════════════════════════════════════════

def _looks_like_sentence(token):
    """True if a 'skill' token is actually a leaked sentence/bullet."""
    t = token.strip()
    words = t.split()
    if len(words) > 6:
        return True
    if len(t) > 45:
        return True
    if words:
        first = words[0].strip(string.punctuation).lower()
        if first in ACTION_VERBS:
            return True
    if t.endswith('.') and len(words) > 2:
        return True
    return False


def parse_skills(lines):
    skills = []
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        # Split on common delimiters INCLUDING colon (fixes "Frontend:HTML" joins)
        tokens = re.split(r'[,;|•·◦▪\-\u2013\u2014/:\n\t]+', line)
        for t in tokens:
            t = re.sub(r'^[\d]+[.\)]\s*', '', t.strip().strip('()[]').strip())
            t = re.sub(r'^[•·▪◦]\s*', '', t).strip()
            if not t or len(t) <= 1 or len(t) > 60:
                continue
            if re.match(r'^\[.*REDACTED\]$', t):
                continue
            if t.lower() in SKILL_CATEGORY_LABELS:
                continue
            if _looks_like_sentence(t):
                continue
            skills.append(t)

    seen = set()
    result = []
    for s in skills:
        key = s.lower()
        if key not in seen:
            seen.add(key)
            result.append(s)
    return result


# ══════════════════════════════════════════════════════════
#  EXPERIENCE PARSER
# ══════════════════════════════════════════════════════════

DATE_RANGE_RE = re.compile(
    r'((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[\s,.\-]*\d{4})'
    r'\s*[-\u2013\u2014to]+\s*'
    r'((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[\s,.\-]*\d{4}'
    r'|Present|Till\s+Date|Current|Now)',
    re.IGNORECASE
)
YEAR_RANGE_RE = re.compile(
    r'\b(20\d{2}|19\d{2})\s*[-\u2013\u2014to]+\s*(20\d{2}|19\d{2}|Present|Current|Now)\b',
    re.IGNORECASE
)
HAS_DATE_RE = re.compile(
    r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[\s,.\-]*\d{4}'
    r'|(?:20\d{2}|19\d{2})',
    re.IGNORECASE
)


def extract_duration(text):
    m = DATE_RANGE_RE.search(text)
    if m:
        return m.group(0)
    m = YEAR_RANGE_RE.search(text)
    if m:
        return m.group(0)
    return None


def parse_experience(lines):
    entries = []
    current = None

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        duration = extract_duration(stripped)
        has_date = bool(HAS_DATE_RE.search(stripped))
        is_bullet = bool(re.match(r'^[•·▪◦\-\u2013\u2014\u25ba>\*]\s+', stripped))

        if has_date and len(stripped) < 120 and not is_bullet:
            if current:
                entries.append(current)
            current = {
                "company": "", "role": "", "duration": duration or "",
                "responsibilities": [], "_raw_header": stripped,
            }
        elif current is None:
            current = {
                "company": "", "role": "", "duration": "",
                "responsibilities": [], "_raw_header": stripped,
            }
        else:
            resp = re.sub(r'^[•·▪◦\-\u2013\u2014\u25ba>\*]+\s*', '', stripped)
            if resp:
                current["responsibilities"].append(resp)

    if current:
        entries.append(current)

    for e in entries:
        header = e.pop("_raw_header", "")
        header_clean = DATE_RANGE_RE.sub('', header)
        header_clean = YEAR_RANGE_RE.sub('', header_clean).strip(' |\u2013\u2014-,')
        parts = re.split(r'\s*[\|,@\u2013\u2014]\s+|\s+at\s+', header_clean)
        parts = [p.strip() for p in parts if p.strip()]
        if len(parts) >= 2:
            e["role"] = parts[0]
            e["company"] = parts[1]
        elif parts:
            e["role"] = parts[0]
        if not e["duration"]:
            e["duration"] = extract_duration(header) or ""

    return [e for e in entries if e.get("role") or e.get("company") or e.get("responsibilities")]


# ══════════════════════════════════════════════════════════
#  PROJECTS PARSER
# ══════════════════════════════════════════════════════════

def extract_technologies(text):
    found = []
    text_lower = text.lower()
    for tech in TECH_WORDS:
        pattern = r'(?<![a-zA-Z])' + re.escape(tech) + r'(?![a-zA-Z])'
        if re.search(pattern, text_lower):
            found.append(tech)
    return sorted(set(found))


def _extract_metadata_and_clean(lines):
    """Pull 'Tech Stack: X, Y' style metadata lines out of a project body."""
    desc_lines = []
    explicit_techs = []
    for line in lines:
        m = re.match(r'^([A-Za-z][A-Za-z &/\-]{1,30}?):\s*(.+)$', line)
        if m and PROJECT_METADATA_LABELS_RE.match(m.group(1).strip()):
            vals = re.split(r'[,;|/]+', m.group(2))
            explicit_techs.extend(v.strip() for v in vals if v.strip())
            continue
        desc_lines.append(line)
    return desc_lines, explicit_techs


def _build_project_from_block(block_lines):
    if not block_lines:
        return None
    title_line = re.sub(r'^[•·▪◦\-\u2013\u2014\u25ba>\*\d\.\)]+\s*', '', block_lines[0].strip())
    body_lines = [l.strip() for l in block_lines[1:] if l.strip()]
    body_lines = [re.sub(r'^[•·▪◦\-\u2013\u2014\u25ba>\*]+\s*', '', l) for l in body_lines]

    desc_lines, explicit_techs = _extract_metadata_and_clean(body_lines)
    description = " ".join(desc_lines[:8])

    auto_techs = extract_technologies(title_line + " " + description)
    seen = {t.lower() for t in explicit_techs}
    technologies = list(explicit_techs)
    for t in auto_techs:
        if t.lower() not in seen:
            technologies.append(t)
            seen.add(t.lower())

    if not title_line and not description:
        return None
    return {"name": title_line, "description": description, "technologies": technologies}


def _is_project_title_line(line):
    if not line or len(line) > 80:
        return False
    if line.rstrip().endswith('.'):
        return False
    if YEAR_RANGE_RE.search(line):
        return False
    m = re.match(r'^([A-Za-z][A-Za-z &/\-]{1,30}?):', line)
    if m and PROJECT_METADATA_LABELS_RE.match(m.group(1).strip()):
        return False
    words = line.split()
    if not words or len(words) > 10:
        return False
    first_word = words[0].strip(string.punctuation).lower()
    if first_word in ACTION_VERBS:
        return False
    cap_words = sum(1 for w in words if w[:1].isupper())
    return (cap_words / len(words)) >= 0.6


def parse_projects(lines):
    # Strategy 1: blank-line separated blocks (most reliable when present)
    blocks = []
    current_block = []
    for line in lines:
        if line.strip() == "":
            if current_block:
                blocks.append(current_block)
                current_block = []
        else:
            current_block.append(line)
    if current_block:
        blocks.append(current_block)

    if len(blocks) >= 2:
        projects = [_build_project_from_block(b) for b in blocks]
        return [p for p in projects if p]

    # Strategy 2: no blank-line separation -> split via title-detection heuristic
    flat_lines = blocks[0] if blocks else [l for l in lines if l.strip()]
    sub_blocks = []
    current = []
    for line in flat_lines:
        stripped = line.strip()
        if not stripped:
            continue
        if _is_project_title_line(stripped) and current:
            sub_blocks.append(current)
            current = [stripped]
        else:
            current.append(stripped)
    if current:
        sub_blocks.append(current)

    projects = [_build_project_from_block(b) for b in sub_blocks]
    return [p for p in projects if p]


# ══════════════════════════════════════════════════════════
#  MAIN PIPELINE
# ══════════════════════════════════════════════════════════

def process_resume(raw_text):
    lines = raw_text.splitlines()
    candidate_name = find_candidate_name(lines)
    masked_text = mask_pii(raw_text, candidate_name)
    masked_lines = masked_text.splitlines()
    sections = split_into_sections(masked_lines)

    return {
        "masked_resume": masked_text,
        "skills": parse_skills(sections.get("skills", [])),
        "experience": parse_experience(sections.get("experience", [])),
        "projects": parse_projects(sections.get("projects", [])),
        "name_found": candidate_name or "Not detected",
    }


