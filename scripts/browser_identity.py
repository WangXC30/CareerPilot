"""Transport identity for historical multi-owner regression fixtures, never product UI."""


def connect_browser(page, credential):
    page.set_extra_http_headers({"Authorization": f"Bearer {credential}"})
    page.evaluate("initializeWorkspace()")
