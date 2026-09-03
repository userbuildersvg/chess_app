"""
Langflow configuration
"""

import os
from dataclasses import dataclass

@dataclass
class LangflowConfig:
    url: str
    api_key: str
    username: str
    password: str
    timeout: int = 30
    max_retries: int = 3
    
    @classmethod
    def from_env(cls) -> 'LangflowConfig':
        # Detect if running on Hugging Face Spaces
        is_hf_spaces = os.getenv("SPACE_ID") is not None
        
        if is_hf_spaces:
            # On HF Spaces, Langflow runs locally on port 7861
            langflow_url = "http://127.0.0.1:7861"
        else:
            # Local development - use external Langflow
            langflow_url = os.getenv("LANGFLOW_URL", "http://192.168.1.72:7860")
        
        return cls(
            url=langflow_url,
            api_key=os.getenv("LANGFLOW_API_KEY", ""),
            username=os.getenv("LANGFLOW_USERNAME", "admin"),
            password=os.getenv("LANGFLOW_PASSWORD", "admin123"),
            timeout=int(os.getenv("LANGFLOW_TIMEOUT", "30")),
            max_retries=int(os.getenv("LANGFLOW_MAX_RETRIES", "3"))
        )

# Global configuration instance
langflow_config = LangflowConfig.from_env()