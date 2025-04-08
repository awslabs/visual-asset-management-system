class SafeLogger:
    """
    Mock implementation of the SafeLogger class.
    """
    def __init__(self, service=None, service_name=None):
        self.service = service or service_name
        
    def info(self, message):
        """
        Mock implementation of the info method.
        """
        pass
        
    def warning(self, message):
        """
        Mock implementation of the warning method.
        """
        pass
        
    def error(self, message):
        """
        Mock implementation of the error method.
        """
        pass
        
    def exception(self, message):
        """
        Mock implementation of the exception method.
        """
        pass

# Alias for backward compatibility
safeLogger = SafeLogger
