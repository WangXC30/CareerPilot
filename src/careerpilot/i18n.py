"""Explicit locale negotiation and server-side UI message resources.

Only fixed system explanations are localized here. Owner-provided content, source
quotes and stored business records are never translated or rewritten.
"""

from __future__ import annotations

DEFAULT_LOCALE = "zh-CN"
SUPPORTED_LOCALES = ("zh-CN", "en-US")

# Fixed success explanations are localized by HTTP adapters after saving records.
# In particular, goal preview snapshots retain their original stored content.
GROWTH_MESSAGES_EN = {
    "source_search": (
        "Search uses the registered official source index only, not a live web search. "
        "Check the original source and its review date."
    ),
    "profile_preview": (
        "Saving retains history. Review your existing plan; it is not overwritten automatically."
    ),
    "goal_preview": (
        "Confirmation switches the primary goal and retains previous goals and history. "
        "Review your study plan and job matches."
    ),
    "goal_plan": (
        " Current study plan v{version}; locked or completed tasks retained: {protected}. "
        "Any new plan requires a separate confirmation."
    ),
}


def plan_preview(result: dict, locale: str) -> dict:
    """Localize a response copy, retaining the stored preview and candidate."""
    if not result.get("validation_error"):
        return result
    problem = result["validation_error"]
    return {
        **result,
        "validation_error": {
            **problem,
            "message": error_message(problem["code"], problem["message"], 422, locale),
            "message_locale": locale,
        },
    }


def assistant_result(result: dict, locale: str) -> dict:
    """Present fixed tool summaries without changing input, steps or stored results."""
    if locale != "en-US":
        return result
    status = result["status"]
    output = result.get("result") or {}
    if status == "success":
        tool = next((step.get("tool") for step in result["steps"] if step.get("tool")), "")
        summaries = {
            "growth.progress": "Read current skill evidence and study plan.",
            "growth.diagnose": (
                f"Analyzed {len(output.get('skills', []))} skill requirements "
                "with original job and evidence references."
            ),
            "growth.plan": (
                f"Generated a draft with {len(output.get('tasks', []))} tasks. "
                "Review and publish it yourself."
            ),
            "growth.replan": "Rescheduling differences are ready for you to accept or reject.",
            "growth.resources": (
                f"Found {len(output.get('results', []))} resources in the official source index."
            ),
        }
        return {**result, "summary": summaries.get(tool, "Tool finished. Review its result.")}
    if status in {"failed", "cancelled"}:
        code = output.get("code", "TASK_CANCELLED")
        message = error_message(code, "", 422, locale)
        return {**result, "summary": message, "result": {**output, "message": message}}
    return {**result, "summary": "Calling growth tools." if status == "running" else "Queued."}


# Generic, accurate English explanations keyed by stable error code. Codes reused for
# several situations use a wording that stays true for every Chinese variant.
ERROR_MESSAGES_EN: dict[str, str] = {
    "MODEL_PREVIEW_INVALID": (
        "Invalid dataset or frozen observations. Check versions and complete steps."
    ),
    "ACCOUNT_DELETING": "This account is being deleted; GitHub access is unavailable.",
    "ACTIVE_PLAN_REQUIRED": "Publish or select your current study plan first.",
    "ALREADY_ATTEMPTED": "This application version has already been rehearsed.",
    "ALREADY_CONFIRMED": "This material is already confirmed. Edit the related memory instead.",
    "ALREADY_SUBMITTED": "This attempt has already been submitted. Start a new one.",
    "AMBIGUOUS_LOCAL_TIME": "This local time occurs twice. Enter the time in UTC instead.",
    "AMBIGUOUS_SKILL_ALIAS": "A skill alias cannot duplicate another skill.",
    "ANSWERS_REQUIRED": "Answer every question.",
    "ANSWER_REQUIRED": "Enter a written answer.",
    "APPEAL_REQUIRED": "Finish the practice and save your own review request first.",
    "APPLICATION_CLOSED": "This application is closed. Check your actual application record.",
    "APPROVAL_CLOSED": "This approval was already used or revoked. Check the rehearsal history.",
    "APPROVAL_INVALID": "The approval is invalid, expired or already used.",
    "APPROVAL_REQUIRED": "This action requires a specific confirmation.",
    "ASR_FAILED": (
        "No usable speech draft was produced. Play back and type the text yourself; "
        "your reviewed text was not changed."
    ),
    "ASR_UNAVAILABLE": "The offline English transcription tool is unavailable.",
    "ASSESSMENT_UNAVAILABLE": "No current assessment is available for this skill.",
    "ASSET_TOO_LARGE": "Text deliverables are limited to 64 KiB.",
    "ASSET_UNAVAILABLE": "This deliverable is unavailable or does not belong to the project.",
    "BANK_UNAVAILABLE": "The question bank version changed. Start again.",
    "BUDGET_EXCEEDED": "The weekly plan exceeds your study budget. Adjust tasks or budget.",
    "BUDGET_REQUIRED": "The weekly study budget must be greater than zero.",
    "CALENDAR_STALE": "The event changed. Select it again before exporting.",
    "CHECKLIST_REQUIRED": "Confirm facts, contact details, job requirements and privacy checks.",
    "CHOICE_REQUIRED": "Choose an answer for the basic question first.",
    "COMMENTS_PRIVATE": "Comments require space membership.",
    "CONFLICT": "Write conflict. Refresh, or retry with the same idempotency key.",
    "CONSENT_REQUIRED": "Grant consent for this purpose first.",
    "CONSENT_REVOKED": "Consent was revoked.",
    "CREDENTIAL_DETECTED": "Remove credentials before submitting.",
    "DAILY_TIME_EXCEEDED": "Recorded time for one day cannot exceed 1440 minutes.",
    "DATE_CONFLICT": "Tasks cannot be scheduled before the plan starts.",
    "DEADLINE_CONFLICT": "The plan cannot be completed before the deadline.",
    "DEADLINE_REQUIRED": "Deadline reminders need an explicit deadline and must not be later.",
    "DEPENDENCY_CONFLICT": "A prerequisite task cannot follow the task that depends on it.",
    "DUPLICATE_APPLICATION": "An active application already exists for this job.",
    "DUPLICATE_FACT": "Select each item once without duplicates.",
    "DUPLICATE_FIELD": "Name and bio are limited to one entry each.",
    "EMPTY_FACT": "Enter the statement you want to confirm.",
    "EMPTY_JD": "The job description cannot be empty.",
    "EMPTY_KEYWORD": "The keyword cannot be empty.",
    "EMPTY_MATERIAL": "The material cannot be empty.",
    "EMPTY_SOURCE": "Provide a source link or an excerpt you are allowed to process.",
    "EVIDENCE_REQUIRED": "Skill verification requires a valid assessment or reviewed evidence.",
    "EXPORT_UNAVAILABLE": "PDF rendering is unavailable. Retry or export Word.",
    "EXTRACTION_UNAVAILABLE": (
        "Extraction failed, the file is protected or has no readable text. "
        "You can provide an authorized excerpt manually."
    ),
    "EXTRACTOR_UNAVAILABLE": "Use the Compose service that includes the local extraction tools.",
    "FACT_CONFIRMATION_REQUIRED": "Only currently confirmed statements can be used.",
    "FORBIDDEN": "You do not have permission for this operation.",
    "FUTURE_STUDY_LOG": "You cannot record study time in the future.",
    "GITHUB_ACCESS_DENIED": "GitHub denied access or rate-limited the request. Retry later.",
    "GITHUB_ALREADY_BOUND": "Unlink the current account before authorizing again.",
    "GITHUB_EXCESS_SCOPE": (
        "The granted scope exceeds public read-only needs and was not saved. "
        "Revoke the old grant and retry."
    ),
    "GITHUB_INVALID_RESPONSE": "GitHub returned an invalid response; previous results were kept.",
    "GITHUB_INVALID_STATE": "The authorization expired, was used or belongs to another account.",
    "GITHUB_KEY_CHANGED": "The stored credential cannot be read. Unlink and authorize again.",
    "GITHUB_NOT_BOUND": "Link GitHub first.",
    "GITHUB_NOT_CONFIGURED": "The GitHub OAuth app or encryption key is not configured.",
    "GITHUB_REAUTHORIZE": "The authorization is no longer valid. Unlink and authorize again.",
    "GITHUB_SYNC_RATE_LIMIT": "You can sync at most once per minute. Try again later.",
    "GITHUB_UNAVAILABLE": "GitHub is unavailable; this sync was not saved.",
    "HTTP_ERROR": "The request could not be processed.",
    "IDEMPOTENCY_CONFLICT": "This idempotency key was already used for a different request.",
    "INACTIVE_JOB": "Closed or merged jobs cannot be compared.",
    "INCOMPLETE_TASK": "Tasks must include resources and an acceptance method.",
    "INCOMPLETE_TRANSLATION": "Map every fact exactly once.",
    "INDEPENDENT_REVIEW_REQUIRED": "Another operator or administrator must review this.",
    "INTERNAL_ERROR": "Internal error.",
    "INTERVIEW_FINISHED": "This session has ended.",
    "INTERVIEW_INCOMPLETE": "Finish the session first; skipped questions are kept explicitly.",
    "INVALID_ACTION": "Unknown task action.",
    "INVALID_APPROVAL": "An approval cannot be valid for more than five minutes.",
    "INVALID_ASSET": "Provide a plain-text file name without a path, and its content.",
    "INVALID_AUDIO": "Only WAV/WebM recordings up to 2 MiB with valid encoding are accepted.",
    "INVALID_CATALOGUE_KIND": "Unsupported public catalogue type.",
    "INVALID_CODE": "Source files must be 1 to 32768 bytes.",
    "INVALID_CURSOR": "Invalid timeline page marker.",
    "INVALID_DAG": "The prerequisite graph has a cycle or an unknown node.",
    "INVALID_DATE": "The practice date cannot be in the future.",
    "INVALID_FACT": "The selected statement is invalid for this request.",
    "INVALID_FIELDS": "A shared field is outside the allowed range.",
    "INVALID_GITHUB_REFERENCE": "Only credential-free GitHub commit or PR links are supported.",
    "INVALID_LOCAL_TIME": "This local time does not exist in the time zone (daylight saving).",
    "INVALID_MATCHING_POLICY": "Weights must total 100 and use fixed matching-policy entries.",
    "INVALID_MEDIA": "Invalid media encoding or unsupported file type.",
    "INVALID_MERGE": "These jobs cannot be merged.",
    "INVALID_NAME": "Name statements are limited to 100 characters.",
    "INVALID_OFFER": "The selected option does not exist.",
    "INVALID_PERIOD": "Choose a correctly ordered range of at most 366 days.",
    "INVALID_PLAN": "The plan contains duplicate or invalid tasks.",
    "INVALID_PLAN_DAG": "Task dependencies contain a cycle or a missing task.",
    "INVALID_PROJECT_CONTEXT": "Only project or experience statements in this resume can be used.",
    "INVALID_PUBLIC_DATE": "The checked time must be an ISO time with a time zone.",
    "INVALID_PUBLIC_PAYLOAD": "Public data fields or values do not match this type.",
    "INVALID_PUBLIC_URL": "Public sources need a canonical HTTPS link without query parameters.",
    "INVALID_QUOTE": "Quoted views must point to text within this excerpt.",
    "INVALID_RATING": "Weights are 0–100; ratings are 0–5 or unknown.",
    "INVALID_RECEIPT": "The receipt is invalid or its submission time is in the future.",
    "INVALID_REFERENCE_URL": "Provide an HTTPS source link without accounts or credentials.",
    "INVALID_REPOSITORY": "Provide a public github.com/owner/repository link.",
    "INVALID_RESOLUTION": "Select two different records.",
    "INVALID_SCHEDULE": "The schedule time is invalid or not in the future.",
    "INVALID_SCHEMA": "External schema references are not allowed.",
    "INVALID_SKILL_ALIAS": "Skill names and aliases must be non-empty text without controls.",
    "INVALID_SOURCE_DATE": "The source publication date cannot be in the future.",
    "INVALID_SPAN": "The source span must lie within the original text.",
    "INVALID_SPLIT": "Split time must be greater than zero and less than the original task.",
    "INVALID_STATE": "The current state does not allow this operation.",
    "INVALID_TEXT": "Use valid UTF-8 text.",
    "INVALID_TIMEZONE": "Choose a valid time zone.",
    "INVALID_TRANSITION": "This status change is not allowed.",
    "INVALID_VERDICT": "The evaluation result is invalid or incomplete.",
    "INVALID_WEIGHTS": "Comparison weights must total 100.",
    "INVITATION_UNAVAILABLE": "This account cannot be invited.",
    "JOB_CHANGED": "The job posting changed. Review the tailored material again.",
    "JOB_MISMATCH": "The resume version does not match this job.",
    "JOB_RECHECK_REQUIRED": "The job is closed, expired or pending review. Check it first.",
    "JOB_SELECTION_REQUIRED": "Select 3 to 20 different jobs.",
    "JOB_UNAVAILABLE": "Select a currently active job.",
    "KEY_UNAVAILABLE": "No key file is configured.",
    "LEASE_EXPIRED": "The execution lease has expired.",
    "LOCKED_TASK": "Adjustments cannot override locked or completed tasks.",
    "LOGIN_FAILED": "Incorrect email or password.",
    "LOGIN_RATE_LIMIT": "Too many requests. Try again later.",
    "LOG_WITHDRAWN": "This record has already been withdrawn.",
    "MEDIA_LIMIT": "At most 50 active media indexes. Delete older ones first.",
    "MEDIA_TOO_LARGE": "Media files are limited to 8 MiB.",
    "MODEL_LIMIT": "The model request exceeds the limit.",
    "MODEL_PROVIDER_ERROR": "Model processing failed. Continue manually or retry later.",
    "MODEL_SCHEMA_ERROR": "Model processing failed. Continue manually or retry later.",
    "MODEL_TIMEOUT": "Model processing failed. Continue manually or retry later.",
    "NARRATIVE_REQUIRED": "Describe the actual context, task and your own actions first.",
    "NOT_FOUND": "The resource does not exist, has expired or access was revoked.",
    "NOT_READY": "Database migrations are not ready.",
    "OUTCOME_REQUIRED": "Record the learning outcome.",
    "OWNER_CANNOT_LEAVE": "Space owners should close the space instead.",
    "PLAN_HORIZON": "The budget is too small for an executable plan within two years.",
    "PLAN_REQUIRED": "Specify the plan when recording a task.",
    "PREREQUISITE_REQUIRED": "Complete prerequisite learning or provide valid evidence first.",
    "PREVIEW_CHANGED": "Materials or the job changed. Preview again.",
    "PREVIEW_REQUIRED": "Activate the goal through the career goal preview.",
    "PRIVATE_IDENTITY": "Public drafts cannot include names, emails or phone numbers.",
    "PRIVATE_PLAN": "Study plans can only be shared within a collaboration space.",
    "PROFILE_REQUIRED": "Confirm your student profile first.",
    "PROJECT_ALREADY_PLANNED": "This project is already in the plan.",
    "PROJECT_CHECKS_INCOMPLETE": "Finish the self-report stage and get a current passing receipt.",
    "PROJECT_CONFIRMATION_REQUIRED": (
        "Confirm the STAR statement and get a current passing code receipt."
    ),
    "PROJECT_NOTE_REQUIRED": "Record what actually happened in this stage.",
    "PROJECT_QUEUE_FULL": "At most three submissions can wait for evaluation.",
    "PROJECT_RECEIPT_REQUIRED": "This submission has no currently valid passing receipt.",
    "PROJECT_UNAVAILABLE": "No practice template of this version exists for the job.",
    "PUBLIC_REVIEW_REQUIRED": (
        "Validation passed; publish dependency changes through an independently "
        "reviewed public change request."
    ),
    "QUESTION_MISMATCH": "Answer the current question.",
    "QUESTION_UNAVAILABLE": "This question is not in the current catalogue.",
    "QUIET_AFTER_DEADLINE": "Quiet hours push the reminder past the deadline. Choose earlier.",
    "QUOTA_EXCEEDED": "The usage quota has been reached.",
    "RECEIPT_REQUIRED": "This application status requires a confirmed receipt.",
    "REGISTRATION_UNAVAILABLE": "This email cannot be registered. Try signing in.",
    "REMINDER_FINISHED": "This reminder has ended. Create a new one.",
    "REPLAY_UNAVAILABLE": "This experiment cannot be replayed with its frozen versions.",
    "REQUIREMENTS_REQUIRED": "Choose a job description or goal containing catalogued skills.",
    "RESULT_UNCERTAIN": "A rehearsal is running or its result is unknown. Review it first.",
    "RESUME_REVIEW_REQUIRED": "Resolve resume name, contact or source issues first.",
    "RESUME_TOO_LONG": "A resume version is limited to 20000 characters.",
    "REVIEWER_UNAVAILABLE": "Choose another member who accepted an invitation to your space.",
    "REVIEW_ALREADY_PLANNED": "This review is already in the plan.",
    "REVIEW_ALREADY_SHARED": "An active review grant exists. View it or revoke it first.",
    "REVIEW_FINISHED": "The review result was already saved.",
    "REVIEW_NOTE_REQUIRED": "Enter the basis and limitations of your review.",
    "REVIEW_REQUIRED": "A confirmation or independent review is required first.",
    "REVIEW_UNAVAILABLE": "Only unknown rehearsal results need manual review.",
    "RIGHTS_REQUIRED": "Confirm that you may process this file.",
    "RULE_UNAVAILABLE": "This historical scoring rule cannot run; the original result is kept.",
    "RUN_PENDING": "A subscription check is already pending.",
    "SCOPE_MISMATCH": "A link-only save cannot include excerpts or views.",
    "SESSION_LIMIT": "Each tutoring session allows at most 12 turns.",
    "SHARE_STALE": "The source changed; the owner must share it again.",
    "SHARE_TARGET_REQUIRED": "Choose a space or a public link to share with.",
    "SKILL_REGRESSION_FAILED": "The alias failed source-text or negation checks. Review it.",
    "SOURCE_DISABLED": "This source has not passed access review.",
    "SOURCE_EXPIRED": "The source retention period ended. Delete or import it again.",
    "SOURCE_INVALID_RESPONSE": "The source returned an invalid response; nothing was saved.",
    "SOURCE_RATE_LIMITED": "The source is rate-limited. Retry later or import manually.",
    "SOURCE_SCOPE_DENIED": "The source operation exceeds its reviewed scope.",
    "SOURCE_TOO_LARGE": "The page is too large. Use a link or a manual excerpt.",
    "SOURCE_UNAVAILABLE": "The source is unavailable or no longer valid.",
    "STALE_EVALUATOR": "The evaluator lease has expired.",
    "STALE_FACT": "The statement source changed or was withdrawn. Confirm again.",
    "STALE_PLAN": "Inputs to this plan changed. Generate the plan again.",
    "STALE_PREVIEW": "The underlying data changed. Preview again.",
    "STALE_PROJECT": "The project, deliverables or statements changed. Preview again.",
    "STALE_REVIEW": "The interview review changed or was withdrawn. Preview again.",
    "STALE_WORKFLOW_CONTEXT": "Inputs changed. Keep the old result and start a new review.",
    "SUBSCRIPTION_INACTIVE": "Resume the subscription first.",
    "SUBSCRIPTION_LIMIT": "At most 20 active subscriptions are allowed.",
    "SUBSCRIPTION_PAUSED": "The subscription is paused.",
    "TEMPLATE_UNAVAILABLE": "This template version is unavailable.",
    "TOOL_DENIED": "The assistant is not authorized for this tool call.",
    "TRANSLATION_FACT_MISMATCH": "Numbers or dates differ. Check the translation.",
    "TRUSTED_TEST_REQUIRED": "Implementation milestones require a current isolated test receipt.",
    "UNAUTHENTICATED": "A valid sign-in credential is required.",
    "UNKNOWN_CAREER": "This career direction is not catalogued.",
    "UNKNOWN_SKILL": "This skill or alias is not in the current catalogue.",
    "UNKNOWN_TASK": "Choose a study task within this plan.",
    "UNRELATED_REVIEW": "Review items must match catalogued topics of this session.",
    "UNSAFE_URL": "The URL is not a permitted public HTTPS address.",
    "UNSUPPORTED_TASK": "This task type is not enabled yet.",
    "UPLOAD_REJECTED": "The file was rejected (name, size, encoding or content).",
    "USE_REPLAN": "Protected tasks exist. Use the replanning preview.",
    "VALIDATION_ERROR": "Invalid request fields or headers.",
    "VERSION_CONFLICT": "This record was updated. Refresh and try again.",
    "WRONG_ANSWER_REQUIRED": "Choose a question you answered incorrectly in your practice.",
}

_STATUS_FALLBACK_EN = {
    400: "The request is invalid.",
    401: "A valid sign-in credential is required.",
    403: "You do not have permission for this operation.",
    404: "The resource does not exist.",
    409: "The request conflicts with the current state.",
    422: "The request could not be processed.",
    429: "Too many requests. Try again later.",
    503: "The service is temporarily unavailable. Retry later.",
}


def negotiate(header: str | None) -> str:
    """Pick a supported locale from an Accept-Language header; default zh-CN."""
    ranked: list[tuple[float, int, str]] = []
    for index, part in enumerate((header or "").split(",")):
        pieces = part.strip().split(";")
        tag = pieces[0].strip().lower()
        if not tag:
            continue
        quality = 1.0
        for parameter in pieces[1:]:
            name, _, value = parameter.strip().partition("=")
            if name.strip() == "q":
                try:
                    quality = float(value)
                except ValueError:
                    quality = 0.0
        if quality <= 0:
            continue
        if tag == "*":
            locale = DEFAULT_LOCALE
        elif tag.split("-")[0] == "en":
            locale = "en-US"
        elif tag.split("-")[0] == "zh":
            locale = "zh-CN"
        else:
            continue
        ranked.append((-quality, index, locale))
    return min(ranked)[2] if ranked else DEFAULT_LOCALE


def error_message(code: str, message: str, status: int, locale: str) -> str:
    """Return the fixed explanation for a locale without exposing raw input."""
    if locale != "en-US":
        return message
    return ERROR_MESSAGES_EN.get(code) or _STATUS_FALLBACK_EN.get(
        status, "The request could not be completed."
    )
