import openai
import os

openai.api_key = os.getenv("OPENAI_API_KEY")

def match_images_to_issues(issues, page_texts):
    """
    issues: dict like {roof: "...", plumbing: "..."}
    page_texts: list of text per page
    """

    prompt = f"""
You are an expert home inspection analyst.

We have inspection issues:
{issues}

And we have extracted text from PDF pages:
{page_texts}

For each issue, return the BEST matching page index (0-based).

Return JSON only like:
{{
  "roof": 0,
  "plumbing": 1,
  "electrical": 2,
  "hvac": 3,
  "foundation": 0
}}
"""

    response = openai.ChatCompletion.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0
    )

    content = response.choices[0].message.content
    return eval(content)
