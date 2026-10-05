import json
import os
import re

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def parse_json(text):
    if not text:
        return None
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(t)
    except Exception:
        pass
    m = re.search(r"\{.*\}", t, re.S)
    if m:
        try:
            return json.loads(m.group())
        except Exception:
            return None
    return None


class LLM:
    """Thin provider wrapper. Gemini (default), Anthropic, or OpenAI. Env: INQUEST_PROVIDER, INQUEST_MODEL, API keys."""

    def __init__(self, provider=None, model=None, api_key=None):
        self.provider = (provider or os.getenv("INQUEST_PROVIDER", "gemini")).lower()
        if self.provider in ("gemini", "google"):
            from google import genai
            self.provider = "gemini"
            self.model = model or os.getenv("INQUEST_MODEL", "gemini-3.8-flash")
            key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or os.getenv("INQUEST_API_KEY")
            if not key:
                raise ValueError("LLM service is not configured. Please configure the server environment.")
            self.client = genai.Client(api_key=key)
        elif self.provider == "anthropic":
            import anthropic
            self.model = model or os.getenv("INQUEST_MODEL", "claude-sonnet-5-5")
            key = api_key or os.getenv("ANTHROPIC_API_KEY") or os.getenv("INQUEST_API_KEY")
            if not key:
                raise ValueError("LLM service is not configured. Please configure the server environment.")
            self.client = anthropic.Anthropic(api_key=key)
        elif self.provider == "openai":
            import openai
            self.model = model or os.getenv("INQUEST_MODEL", "gpt-4o")
            key = api_key or os.getenv("OPENAI_API_KEY") or os.getenv("INQUEST_API_KEY")
            if not key:
                raise ValueError("LLM service is not configured. Please configure the server environment.")
            self.client = openai.OpenAI(api_key=key)
        else:
            raise ValueError("provider must be 'gemini', 'anthropic' or 'openai'")
        self.n_calls = 0

    def complete(self, system, user, max_tokens=1200):
        self.n_calls += 1
        if self.provider == "gemini":
            import time
            from google.genai import types
            config = types.GenerateContentConfig(
                system_instruction=system,
                temperature=0.0,
                max_output_tokens=max_tokens,
            )
            fallback_models = [self.model, "gemini-3.5-flash-lite", "gemini-flash-latest"] if self.model != "gemini-3.5-flash-lite" else [self.model]
            last_err = None
            for m in fallback_models:
                for attempt in range(2):
                    try:
                        response = self.client.models.generate_content(
                            model=m,
                            contents=user,
                            config=config,
                        )
                        return response.text or ""
                    except Exception as e:
                        last_err = e
                        if "503" in str(e) or "429" in str(e) or "UNAVAILABLE" in str(e):
                            time.sleep(1.0 * (attempt + 1))
                            continue
                        elif "404" in str(e) or "NOT_FOUND" in str(e):
                            break
                        raise
            if last_err:
                raise last_err
        elif self.provider == "anthropic":
            kw = dict(model=self.model, max_tokens=max_tokens, system=system,
                      messages=[{"role": "user", "content": user}])
            try:
                r = self.client.messages.create(temperature=0, **kw)
            except Exception as e:
                if "temperature" in str(e).lower():
                    r = self.client.messages.create(**kw)
                else:
                    raise
            return "".join(b.text for b in r.content if getattr(b, "type", "") == "text")
        r = self.client.chat.completions.create(
            model=self.model, temperature=0, max_tokens=max_tokens,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
        return r.choices[0].message.content
