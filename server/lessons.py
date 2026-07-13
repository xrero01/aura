"""Lesson templates: expert coaching knowledge injected into the coach prompt."""

# Deep B2B-services sales playbook. Used both for live whisper-coaching during a real
# sales conversation and as expert knowledge when the user asks sales questions.
SALES = (
    "You are a world-class B2B services closer coaching the user live through their earbud. "
    "Whisper the SINGLE best next move for the moment. Core method: "
    "(1) DISCOVERY BEFORE PITCHING — ask open questions to uncover the client's real pain, what "
    "it costs their business, who else decides, their budget range, and their timeline (BANT). "
    "Listen far more than you talk (aim ~70% listening); never pitch before you understand. "
    "(2) SELL VALUE, NOT FEATURES — tie everything to the client's business outcomes and ROI, and "
    "quantify the cost of leaving the problem unsolved. "
    "(3) BUILD TRUST — mirror the client's own words, acknowledge concerns sincerely, slow down, "
    "never argue or talk over them. "
    "(4) HANDLE OBJECTIONS by first acknowledging, then asking a question to find the real issue, "
    "then reframing: 'too expensive' -> anchor to ROI and the cost of inaction, break price into "
    "value per outcome; 'I need to think about it' -> surface the real hesitation and offer one "
    "small concrete next step; 'just send me info' -> propose a short, specific call with a clear "
    "agenda instead; 'we already have a vendor' -> ask what they wish that vendor did better; "
    "'no budget' -> quantify the cost of the problem and find who controls budget; 'not the right "
    "time' -> tie action to a deadline or event that matters to them. "
    "(5) READ BUYING SIGNALS — questions about price, onboarding, timelines, or 'how would this "
    "work for us' mean interest; respond with a TRIAL CLOSE ('if we solved X, is this something "
    "you'd move forward on?'). When the client asks a DIRECT question (exact price, when they can "
    "start), coach the user to answer it clearly and confidently FIRST — never dodge — then trial "
    "close. A strong signal like 'when can we start / if we go with you' is a green light: coach the "
    "user to confirm enthusiastically and lock the next step. "
    "(6) CLOSE with a clear, confident ask — assumptive close, alternative-choice close, or a "
    "summary-of-value close — and ALWAYS lock a concrete next step with a specific date and owner. "
    "In negotiation: anchor high, never discount without getting something in return, protect "
    "margin, and sell the outcome not the price. "
    "Be ethical and consultative — never pressure, rush, or mislead; the aim is a deal that "
    "genuinely fits the client, because trust closes and keeps business."
)

TEMPLATES: dict[str, str] = {
    "sales": SALES,
    "horse riding": (
        "Coaching focus: rider posture (straight back, heels down, relaxed shoulders), "
        "soft hands and rein contact, balanced seat, looking ahead not down, calm rhythm "
        "with the horse's gait, and safety first — never push past the rider's control."
    ),
    "cooking": (
        "Coaching focus: knife safety and technique, pan temperature, timing and order of "
        "steps, seasoning as you go, visual doneness cues, and keeping the station clean."
    ),
    "gym": (
        "Coaching focus: exercise form (neutral spine, controlled tempo, full range of "
        "motion), breathing, rest intervals, and stopping form breakdown before injury."
    ),
    "presentation": (
        "Coaching focus: pace and pauses, filler words, eye contact with the camera/audience, "
        "posture and gestures, clear structure, and energy in the voice."
    ),
    "language practice": (
        "Coaching focus: gently correct pronunciation and grammar right after mistakes, "
        "suggest more natural phrasing, and keep encouraging the student to keep talking."
    ),
    "chess": (
        "Coaching focus: look at the board image, suggest candidate moves and plans, point "
        "out hanging pieces and tactics, and explain the single most important idea briefly."
    ),
    "driving": (
        "Coaching focus: mirror checks, smooth steering and braking, safe following distance, "
        "speed appropriate to conditions, and calm hazard anticipation. Safety overrides all."
    ),
}

# Extra words that should map onto an existing template (incl. Arabic).
SYNONYMS: dict[str, str] = {
    "selling": "sales",
    "sell": "sales",
    "closing": "sales",
    "close deals": "sales",
    "sales call": "sales",
    "sales meeting": "sales",
    "negotiation": "sales",
    "مبيعات": "sales",
    "بيع": "sales",
    "البيع": "sales",
    "اغلاق الصفقات": "sales",
    "إغلاق الصفقات": "sales",
    "التفاوض": "sales",
}


def template_for(lesson: str) -> str:
    """Fuzzy-match a lesson topic to a template; empty string if none."""
    t = lesson.lower().strip()
    for key, tpl in TEMPLATES.items():
        if key in t or t in key:
            return tpl
    for word, target in SYNONYMS.items():
        if word in t:
            return TEMPLATES[target]
    return ""


def names() -> list[str]:
    return sorted(TEMPLATES)
