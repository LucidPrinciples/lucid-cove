"""
Ollama LLM Transport for Pipecat

Uses direct HTTP requests to Ollama API at host.docker.internal:11434.
Streams response tokens for real-time conversation flow.
"""

import logging
import json
import httpx
from typing import AsyncIterator, Optional, List, Dict
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ChatMessage:
    """A message in the conversation."""
    role: str  # 'system', 'user', 'assistant'
    content: str


class OllamaLLMTransport:
    """
    LLM transport using local Ollama instance.
    
    Features:
    - Streaming text generation via Ollama API
    - Conversation context management
    - Configurable model and parameters
    """
    
    DEFAULT_BASE_URL = "http://host.docker.internal:11434"
    DEFAULT_MODEL = "qwen3:30b-a3b"
    
    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        temperature: float = 0.7,
        max_tokens: int = 512,
        system_prompt: Optional[str] = None
    ):
        self.base_url = base_url.rstrip('/')
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.system_prompt = system_prompt or (
            "You are a helpful voice assistant. Keep responses concise "
            "and natural for spoken conversation."
        )
        self.client = httpx.AsyncClient(timeout=60.0)
        self.is_initialized = False
        self.conversation_history: List[Dict] = []
    
    async def check_ollama_available(self) -> bool:
        """Check if Ollama is running and reachable."""
        try:
            response = await self.client.get(f"{self.base_url}/api/tags")
            if response.status_code == 200:
                data = response.json()
                models = [m.get('name') for m in data.get('models', [])]
                logger.info(f"Ollama available. Models: {models[:5]}")
                return True
            return False
        except Exception as e:
            logger.warning(f"Ollama not available: {e}")
            return False
    
    def initialize(self) -> bool:
        """Initialize the LLM transport."""
        import asyncio
        try:
            # Check synchronously for initialization
            loop = asyncio.get_event_loop()
            import httpx as _hx
            r = _hx.get(f"{self.base_url}/api/tags", timeout=10)
            available = r.status_code == 200
            if available:
                self.is_initialized = True
                logger.info(f"Ollama LLM transport initialized with model: {self.model}")
                return True
            else:
                logger.error(f"Ollama not available at {self.base_url}")
                return False
        except Exception as e:
            logger.error(f"Failed to initialize Ollama transport: {e}")
            return False
    
    async def generate(
        self,
        prompt: str,
        stream: bool = True
    ) -> AsyncIterator[str]:
        """
        Generate text response from Ollama.
        
        Args:
            prompt: User input text
            stream: If True, yield tokens as they're generated
            
        Yields:
            Text tokens (if streaming) or full response
        """
        if not self.is_initialized:
            logger.error("Ollama transport not initialized")
            return
        
        # Build conversation context
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": prompt}
        ]
        
        # Add recent history (last 4 exchanges)
        if self.conversation_history:
            messages = (
                [{"role": "system", "content": self.system_prompt}] +
                self.conversation_history[-4:] +
                [{"role": "user", "content": prompt}]
            )
        
        request_data = {
            "model": self.model,
            "messages": messages,
            "stream": stream,
            "options": {
                "temperature": self.temperature,
                "num_predict": self.max_tokens
            }
        }
        
        try:
            if stream:
                async with self.client.stream(
                    "POST",
                    f"{self.base_url}/api/chat",
                    json=request_data
                ) as response:
                    response.raise_for_status()
                    
                    full_response = ""
                    async for line in response.aiter_lines():
                        if line.strip():
                            try:
                                data = json.loads(line)
                                if "message" in data:
                                    content = data["message"].get("content", "")
                                    if content:
                                        full_response += content
                                        yield content
                                
                                if data.get("done", False):
                                    break
                            except json.JSONDecodeError:
                                continue
                    
                    # Store in conversation history
                    self.conversation_history.append({"role": "user", "content": prompt})
                    self.conversation_history.append({
                        "role": "assistant",
                        "content": full_response
                    })
            else:
                # Non-streaming request
                response = await self.client.post(
                    f"{self.base_url}/api/chat",
                    json=request_data
                )
                response.raise_for_status()
                data = response.json()
                
                content = data.get("message", {}).get("content", "")
                yield content
                
                # Store in history
                self.conversation_history.append({"role": "user", "content": prompt})
                self.conversation_history.append({
                    "role": "assistant",
                    "content": content
                })
                
        except httpx.HTTPStatusError as e:
            logger.error(f"Ollama HTTP error: {e.response.status_code} - {e.response.text}")
            yield "[Error: LLM service unavailable]"
        except Exception as e:
            logger.error(f"Ollama generation error: {e}")
            yield "[Error: Could not generate response]"
    
    async def generate_simple(self, prompt: str) -> str:
        """
        Simple non-streaming generation.
        
        Args:
            prompt: User input text
            
        Returns:
            Complete response text
        """
        response_parts = []
        async for token in self.generate(prompt, stream=False):
            response_parts.append(token)
        return "".join(response_parts)
    
    def clear_history(self):
        """Clear conversation history."""
        self.conversation_history = []
        logger.info("Conversation history cleared")
    
    async def cleanup(self):
        """Cleanup resources."""
        await self.client.aclose()
        self.is_initialized = False
        logger.info("Ollama LLM transport cleaned up")


# Global LLM transport instance
_llm_transport = None

async def get_llm_transport() -> Optional[OllamaLLMTransport]:
    """Get or initialize the global LLM transport."""
    global _llm_transport
    if _llm_transport is None:
        _llm_transport = OllamaLLMTransport()
        if not _llm_transport.initialize():
            _llm_transport = None
    return _llm_transport


if __name__ == "__main__":
    # Test the transport
    import asyncio
    logging.basicConfig(level=logging.INFO)
    
    async def test():
        print("Testing Ollama LLM Transport...")
        transport = OllamaLLMTransport()
        
        if transport.initialize():
            print("Transport initialized!")
            
            print("\nTesting streaming generation:")
            prompt = "Hello! Please say 'Ollama transport is working' and nothing else."
            print(f"Prompt: {prompt}")
            print("Response: ", end="", flush=True)
            
            async for token in transport.generate(prompt):
                print(token, end="", flush=True)
            print()
            
            await transport.cleanup()
        else:
            print("Failed to initialize transport")
    
    asyncio.run(test())