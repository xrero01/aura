"""Lesson templates: expert coaching knowledge injected into the coach prompt."""

TEMPLATES: dict[str, str] = {
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


def template_for(lesson: str) -> str:
    """Fuzzy-match a lesson topic to a template; empty string if none."""
    t = lesson.lower()
    for key, tpl in TEMPLATES.items():
        if key in t or t in key:
            return tpl
    return ""


def names() -> list[str]:
    return sorted(TEMPLATES)
