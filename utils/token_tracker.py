import logging
from typing import Dict

logger = logging.getLogger(__name__)

# Global token tracking variables
prompt_tokens = 0
completion_tokens = 0
total_tokens = 0

def update_token_usage(new_prompt_tokens: int, new_completion_tokens: int):
    global prompt_tokens, completion_tokens, total_tokens
    
    prompt_tokens += new_prompt_tokens
    completion_tokens += new_completion_tokens
    total_tokens += new_prompt_tokens + new_completion_tokens
    
    logger.debug(f"Updated global token usage: prompt={prompt_tokens}, completion={completion_tokens}, total={total_tokens}")

def get_token_usage() -> Dict[str, int]:
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens
    }

def get_cost_estimate() -> float:
    prompt_cost = (prompt_tokens / 1000) * 0.0015
    completion_cost = (completion_tokens / 1000) * 0.002
    return prompt_cost + completion_cost

def reset_counters():
    """Reset all token counters to zero"""
    global prompt_tokens, completion_tokens, total_tokens
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0
    logger.debug("Token usage counters reset to zero")