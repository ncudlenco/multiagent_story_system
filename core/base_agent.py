"""
Base Agent with OpenAI Structured Outputs

Provides foundation for all specialized agents using OpenAI's structured output API.
No manual JSON parsing required - API returns validated Pydantic models directly.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Literal, Optional, Type, TypeVar, Generic
from pydantic import BaseModel
import structlog
from openai import OpenAI
import json
import time

logger = structlog.get_logger()

T = TypeVar('T', bound=BaseModel)


class BaseAgent(ABC, Generic[T]):
    """
    Minimal base agent using OpenAI structured outputs.

    Key features:
    - No manual JSON parsing needed
    - API returns validated Pydantic models directly
    - Automatic schema validation
    - No markdown unwrapping required
    - Type-safe with generics

    All specialized agents extend this class and implement:
    - build_system_prompt(): Define agent role and output format
    - build_user_prompt(): Provide specific task and input data
    """

    def __init__(
        self,
        config: Dict[str, Any],
        agent_name: str,
        output_schema: Type[T],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        use_structured_outputs: bool = True,
        prompt_logger=None,
        reasoning_effort: Literal["minimal", "low", "medium", "high"] = None,
        model: Optional[str] = None
    ):
        """
        Initialize agent.

        Args:
            config: Configuration dictionary (from Config.to_dict())
            agent_name: Name for logging and identification
            output_schema: Pydantic model class for structured output
            temperature: Optional override (uses config default if None)
            max_tokens: Optional override (uses config default if None)
            use_structured_outputs: If True, use OpenAI structured outputs API.
                                   If False, use manual JSON parsing (for complex schemas like GEST)
            prompt_logger: Optional PromptLogger instance for logging prompts/responses
            reasoning_effort: Optional override for reasoning effort level
        """
        self.config = config
        self.agent_name = agent_name
        self.output_schema = output_schema
        self.use_structured_outputs = use_structured_outputs
        self.prompt_logger = prompt_logger

        # OpenAI client
        self.client = OpenAI(api_key=config['openai']['api_key'])
        self.model = model if model is not None else config['openai']['model']
        self.temperature = temperature if temperature is not None else config['openai']['temperature']
        self.max_tokens = max_tokens if max_tokens is not None else config['openai']['max_tokens']
        self.reasoning_effort = reasoning_effort if reasoning_effort is not None else config['openai']['reasoning_effort']

        logger.info(
            "agent_initialized",
            agent=agent_name,
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            output_schema=output_schema.__name__,
            use_structured_outputs=use_structured_outputs,
            prompt_logging_enabled=prompt_logger is not None
        )

    @abstractmethod
    def build_system_prompt(self, context: Dict[str, Any]) -> str:
        """
        Build system prompt defining agent role.

        Should include:
        - Agent's role and responsibility in the pipeline
        - Output format description (matches output_schema)
        - Constraints and rules the agent must follow
        - Any domain-specific guidance

        Args:
            context: Context dictionary with relevant information

        Returns:
            System prompt string
        """
        pass

    @abstractmethod
    def build_user_prompt(self, context: Dict[str, Any]) -> str:
        """
        Build user prompt with specific task and data.

        Should include:
        - Specific task for this execution
        - Input data from context (previous level GEST, capabilities, etc.)
        - Any examples or references
        - Explicit output instructions

        Args:
            context: Context dictionary with input data

        Returns:
            User prompt string
        """
        pass

    def call_llm(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        iteration: Optional[int] = None,
        context: Optional[Dict[str, Any]] = None
    ) -> T:
        """
        Call OpenAI API with optional structured output.

        Structured mode (use_structured_outputs=True):
          - Uses beta.chat.completions.parse() API
          - Automatic schema validation
          - Only works with simple schemas (no Dict[str, BaseModel])

        Unstructured mode (use_structured_outputs=False):
          - Injects JSON schema into system prompt
          - Uses regular chat.completions.create() API
          - Manual JSON parsing + Pydantic validation
          - Works with all schemas including GEST

        Args:
            system_prompt: System prompt defining role
            user_prompt: User prompt with task
            temperature: Optional override
            max_tokens: Optional override
            iteration: Optional iteration number (for prompt logging)
            context: Optional context dict (for prompt logging, e.g., scene_id)

        Returns:
            Validated Pydantic model instance (type matches output_schema)

        Raises:
            OpenAI API errors (rate limit, invalid key, etc.)
            ValueError: JSON parsing or Pydantic validation errors
        """
        # Start timing
        start_time = time.time()

        # For unstructured mode, inject schema into system prompt
        if not self.use_structured_outputs:
            schema_dict = self.output_schema.model_json_schema()
            schema_str = json.dumps(schema_dict, indent=2)
            system_prompt += f"\n\nOUTPUT JSON SCHEMA:\n```json\n{schema_str}\n```\n\nYour response must be valid JSON matching this exact schema."

        # Build API parameters
        api_params = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "reasoning_effort": self.reasoning_effort
        }

        # Temperature (GPT-5 compatible)
        temp = temperature if temperature is not None else self.temperature
        if temp is not None and temp != 1.0:
            api_params["temperature"] = temp

        # Token limit (GPT-5 compatible)
        token_limit = max_tokens if max_tokens is not None else self.max_tokens
        if token_limit is not None:
            api_params["max_completion_tokens"] = token_limit

        # BRANCH: Choose API based on mode
        response_raw = None
        if self.use_structured_outputs:
            # STRUCTURED MODE: Beta parse API
            api_params["response_format"] = self.output_schema
            response = self.client.beta.chat.completions.parse(**api_params)
            parsed_result = response.choices[0].message.parsed

        else:
            # UNSTRUCTURED MODE: Manual parsing with schema injection
            api_params["response_format"] = {"type": "json_object"}
            response = self.client.chat.completions.create(**api_params)

            # Parse JSON
            content = response.choices[0].message.content
            response_raw = content  # Save for logging
            try:
                data = json.loads(content)
            except json.JSONDecodeError as e:
                logger.error(
                    "json_parse_error",
                    agent=self.agent_name,
                    error=str(e),
                    content_preview=content[:500]
                )
                raise ValueError(f"Failed to parse JSON from {self.agent_name}: {e}")

            # Validate with Pydantic
            try:
                parsed_result = self.output_schema.model_validate(data)
                logger.debug("pydantic_validation_success", agent=self.agent_name)
            except Exception as e:
                logger.error(
                    "pydantic_validation_error",
                    agent=self.agent_name,
                    error=str(e),
                    data_keys=list(data.keys()) if isinstance(data, dict) else "not_dict"
                )
                raise ValueError(f"Pydantic validation failed for {self.agent_name}: {e}")

        # Calculate execution time
        execution_time = time.time() - start_time

        # Extract token usage from response (FREE metadata)
        token_usage = None
        if hasattr(response, 'usage') and response.usage:
            token_usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens
            }

        # Log prompt if logger is enabled
        if self.prompt_logger:
            self.prompt_logger.log_prompt(
                agent_name=self.agent_name,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                response_parsed=parsed_result,
                token_usage=token_usage,
                execution_time=execution_time,
                model=self.model,
                temperature=temp if temp is not None else 1.0,
                max_tokens=token_limit,
                response_raw=response_raw,
                iteration=iteration,
                context=context
            )

        return parsed_result

    def execute(
        self,
        context: Dict[str, Any],
        max_retries: int = 3,
        iteration: Optional[int] = None
    ) -> T:
        """
        Execute agent with retry logic.

        Builds prompts, calls LLM, and returns validated output.
        Retries on transient errors (network issues, API timeouts).
        Does NOT retry on: invalid API key, rate limits (raises immediately).

        Args:
            context: Context dictionary with input data
            max_retries: Maximum number of retry attempts
            iteration: Optional iteration number (for prompt logging)

        Returns:
            Validated Pydantic model (type matches output_schema)

        Raises:
            Exception: If all retries exhausted or non-retryable error
        """
        for attempt in range(max_retries):
            try:
                # Build prompts
                system_prompt = self.build_system_prompt(context)
                user_prompt = self.build_user_prompt(context)

                logger.info(
                    "calling_llm",
                    agent=self.agent_name,
                    attempt=attempt + 1,
                    max_retries=max_retries
                )

                # Call LLM with structured output
                result = self.call_llm(
                    system_prompt,
                    user_prompt,
                    iteration=iteration,
                    context=context
                )

                logger.info(
                    "agent_success",
                    agent=self.agent_name,
                    attempt=attempt + 1
                )

                return result

            except Exception as e:
                error_type = type(e).__name__
                error_msg = str(e)

                # Check if error is retryable
                non_retryable_errors = [
                    'AuthenticationError',
                    'PermissionDeniedError',
                    'InvalidAPIKey',
                    'RateLimitError'
                ]

                if error_type in non_retryable_errors:
                    logger.error(
                        "non_retryable_error",
                        agent=self.agent_name,
                        error_type=error_type,
                        error=error_msg
                    )
                    raise

                logger.warning(
                    "agent_retry",
                    agent=self.agent_name,
                    attempt=attempt + 1,
                    max_retries=max_retries,
                    error=error_msg,
                    error_type=error_type
                )

                if attempt == max_retries - 1:
                    logger.error(
                        "agent_failed",
                        agent=self.agent_name,
                        total_attempts=max_retries,
                        error=error_msg,
                        error_type=error_type,
                        exc_info=True
                    )
                    raise
