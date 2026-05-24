import sys
from unittest.mock import MagicMock

# Stub AWS and HTTP clients before handler is imported
sys.modules["boto3"] = MagicMock()
sys.modules["requests"] = MagicMock()
