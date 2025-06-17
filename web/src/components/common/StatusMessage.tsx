/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect, createContext, useContext, ReactNode } from "react";
import { Alert, AlertProps } from "@cloudscape-design/components";

interface StatusMessageProps {
  message: ReactNode;
  type: AlertProps.Type;
  dismissible?: boolean;
  autoDismiss?: boolean;
  dismissTimeout?: number;
  onDismiss?: () => void;
}

interface StatusMessageContextType {
  showMessage: (props: StatusMessageProps) => void;
  clearMessage: () => void;
  currentMessage: StatusMessageProps | null;
}

const defaultContext: StatusMessageContextType = {
  showMessage: () => {},
  clearMessage: () => {},
  currentMessage: null,
};

export const StatusMessageContext = createContext<StatusMessageContextType>(defaultContext);

export const useStatusMessage = () => useContext(StatusMessageContext);

interface StatusMessageProviderProps {
  children: ReactNode;
}

export const StatusMessageProvider: React.FC<StatusMessageProviderProps> = ({ children }) => {
  const [currentMessage, setCurrentMessage] = useState<StatusMessageProps | null>(null);
  const [timeoutId, setTimeoutId] = useState<NodeJS.Timeout | null>(null);

  const clearMessage = () => {
    setCurrentMessage(null);
    if (timeoutId) {
      clearTimeout(timeoutId);
      setTimeoutId(null);
    }
  };

  const showMessage = (props: StatusMessageProps) => {
    // Clear any existing timeout
    if (timeoutId) {
      clearTimeout(timeoutId);
      setTimeoutId(null);
    }

    setCurrentMessage(props);

    // Set auto-dismiss timeout if enabled
    if (props.autoDismiss) {
      const timeout = props.dismissTimeout || 5000; // Default 5 seconds
      const id = setTimeout(() => {
        setCurrentMessage(null);
        setTimeoutId(null);
      }, timeout);
      setTimeoutId(id);
    }
  };

  useEffect(() => {
    // Cleanup timeout on unmount
    return () => {
      if (timeoutId) {
        clearTimeout(timeoutId);
      }
    };
  }, [timeoutId]);

  return (
    <StatusMessageContext.Provider value={{ showMessage, clearMessage, currentMessage }}>
      {currentMessage && (
        <Alert
          type={currentMessage.type}
          dismissible={currentMessage.dismissible}
          onDismiss={() => {
            clearMessage();
            if (currentMessage.onDismiss) {
              currentMessage.onDismiss();
            }
          }}
        >
          {currentMessage.message}
        </Alert>
      )}
      {children}
    </StatusMessageContext.Provider>
  );
};

export const StatusMessage: React.FC<StatusMessageProps> = (props) => {
  const { showMessage, clearMessage } = useStatusMessage();

  useEffect(() => {
    showMessage(props);
    return () => clearMessage();
  }, [props.message, props.type]); // eslint-disable-line react-hooks/exhaustive-deps

  return null; // This component doesn't render anything itself
};

export default StatusMessage;
