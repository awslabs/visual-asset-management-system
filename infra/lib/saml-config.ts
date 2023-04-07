import * as cognito from "aws-cdk-lib/aws-cognito";

interface SamlSettings {
    name: string;
    cognitoDomainPrefix: string;
    metadata: cognito.UserPoolIdentityProviderSamlMetadata;
    attributeMapping: cognito.AttributeMapping;
}

/**
 * Check the Cloudformation Outputs for the values needed to configure the remote idp
 * - SAML IdP Response URL
 * - SP urn / Audience URI / SP entity ID
 * - WebAppCloudFrontDistributionUrl for callbacks
 */

/**
 * To enable SAML, set this to true and provide the SAML settings below.
 */
export const samlEnabled = false;

export const samlSettings: SamlSettings = {
    // This string is used to identify the saml identify provider in the Cognito User Pool as well as
    // in the user interface layer to authenticate users against that identity provider.
    name: "myidentiyprovidername",
    // This is used as a url prefix to authenticate users with the SAML identity provider.
    // If you domain prefix is mydomainprefix, then your complete domain will be
    // https://mydomainprefix.auth.us-east-1.amazoncognito.com/saml2/idpresponse. This string
    // needs to be provided to your identity provider.
    cognitoDomainPrefix: "mydomainprefix",
    // The metadata url provided by your SAML identity provider.
    // Optionally, the content of the metadata may be provided as a file.
    metadata: {
        metadataType: cognito.UserPoolIdentityProviderSamlMetadataType.URL,
        metadataContent: "https://example.com/samlp/metadata/idp.xml",
    },
    // The attributes that should be mapped back to VAMS when users authenticate
    // with your SAML identity provider.
    attributeMapping: {
        email: cognito.ProviderAttribute.other(
            "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress"
        ),
        fullname: cognito.ProviderAttribute.other(
            "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name"
        ),
    },
};
