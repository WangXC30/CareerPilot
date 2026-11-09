"""Shared bounds for chat execution, receipt recovery and deletion."""

MAX_TOOL_CALLS = 12
# load + twelve model/prepare/approval/tool cycles + final answer, with headroom.
GRAPH_RECURSION_LIMIT = MAX_TOOL_CALLS * 4 + 8
