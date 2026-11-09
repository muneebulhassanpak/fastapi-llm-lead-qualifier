"""The qualification prompt and how a lead is framed for the model.

The system prompt holds every instruction. The lead goes in the user turn as JSON inside
<enquiry> tags, with `<` escaped so the visitor's text cannot close the tag and pose as
instructions.
"""

import json
from importlib.resources import files

from app.llm.base import LeadInput

PROMPT_VERSION = "qualify_v1"
SYSTEM_PROMPT = files("app.llm.prompts").joinpath(f"{PROMPT_VERSION}.md").read_text("utf-8")


def render_enquiry(lead: LeadInput) -> str:
    payload = json.dumps(
        {"source": lead.source, "name": lead.name, "email": lead.email, "message": lead.message},
        ensure_ascii=False,
        indent=2,
    )
    # JSON allows < for "<", so the data is unchanged but cannot contain a closing tag.
    payload = payload.replace("<", "\\u003c")
    return f"<enquiry>\n{payload}\n</enquiry>\n\nQualify this enquiry."
