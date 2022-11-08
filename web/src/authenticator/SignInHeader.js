import { Heading, useTheme } from "@aws-amplify/ui-react";
import logoDarkImageSrc from "../resources/img/logo_dark.svg";

export function SignInHeader() {
  const { tokens } = useTheme();

  return (
    <Heading level={3} padding={`${tokens.space.xl} ${tokens.space.xl} 0`}>
      <img
        style={{ width: "100%" }}
        src={logoDarkImageSrc}
        alt="Visual Asset Management System Logo"
      />
    </Heading>
  );
}
