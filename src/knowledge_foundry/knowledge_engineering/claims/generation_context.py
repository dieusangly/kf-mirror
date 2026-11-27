"""Claim requests and the historical patchable token-count transport."""
from urllib.request import urlopen

from knowledge_foundry.shared.openai_tools import response_payload
from knowledge_foundry.shared.request_counts import (
    COUNT_VERSION, RequestCounter as SharedRequestCounter, _token_count_http_error,
    model_input_budget, usable_input_budget,
)


def generation_payload(config, input_text, prompt, schema):
    config = config['steps']['claim_generation']
    return response_payload(config['model'], config['reasoning_effort'], prompt, input_text,
                            'claim_candidates', schema,
                            config['budget']['reserved_output_tokens'])


class RequestCounter(SharedRequestCounter):
    """Keep transport overrides on the original generation module effective."""

    @staticmethod
    def _open(request):
        return urlopen(request, timeout=90)
