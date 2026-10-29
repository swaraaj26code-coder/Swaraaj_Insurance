"""Turn API errors into messages a non-programmer can act on."""


def explain(exc: Exception) -> str:
    text = f'{getattr(exc, "code", "")} {getattr(exc, "status", "")} {getattr(exc, "message", "") or exc}'
    if "API_KEY_INVALID" in text or "API key not valid" in text:
        return "Your Google API key is not valid. Copy it again from aistudio.google.com/apikey into the .env file and restart."
    if "RESOURCE_EXHAUSTED" in text or " 429" in text:
        return "The free Google quota is used up for now. Wait a minute (or until tomorrow for the daily limit) and try again."
    if "NOT_FOUND" in text or " 404" in text:
        return "The AI model name is not available for your key. Close the app and run the start script again; it will pick a working model."
    if "PERMISSION_DENIED" in text or " 403" in text:
        return "Your key is not allowed to use this model. Run the start script again so it can pick another model."
    return f"Connection to Google failed: {text.strip()[:200]}"
