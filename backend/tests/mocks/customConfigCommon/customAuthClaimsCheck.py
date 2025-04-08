# Mock implementation of the SafeLogger class
class SafeLogger:
    def __init__(self, service=None, service_name=None):
        self.service = service or service_name
        
    def info(self, message):
        pass
        
    def warning(self, message):
        pass
        
    def error(self, message):
        pass
        
    def exception(self, message):
        pass

# Create a logger instance
safeLogger = SafeLogger

logger = safeLogger(service="CustomConfigAuthClaimsCheck")

def customAuthClaimsCheckOverride(claims):
    """
    Mock implementation of the customAuthClaimsCheckOverride function.
    """
    return claims
