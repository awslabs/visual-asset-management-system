"""Setup command for VamsCLI."""

import re
from urllib.parse import urlparse

import click

from ..utils.api_client import APIClient
from ..utils.decorators import get_profile_manager_from_context
from ..utils.exceptions import ConfigurationError
from ..utils.json_output import output_status, output_result, output_error, output_warning, output_info
from ..version import get_version


def validate_base_url(url: str) -> bool:
    """Validate base URL format - accepts any HTTP/HTTPS URL."""
    try:
        parsed = urlparse(url)
        # Must have scheme and netloc
        if not parsed.scheme or not parsed.netloc:
            return False

        # Must be HTTP or HTTPS
        return parsed.scheme.lower() in ['http', 'https']
    except Exception:
        return False


# Default REST API deployment stage. The execute-api endpoint serves routes under a stage
# path; a CloudFront/ALB front absorbs it, but a raw execute-api URL must include it.
DEFAULT_API_STAGE = "api"


def normalize_base_url_for_stage(url: str) -> str:
    """Ensure a raw execute-api base URL includes the REST API stage path.

    The REST API is served under a stage (default ``api``), so the real invoke path for a
    route is ``/{stage}{routePath}``. A front (CloudFront/ALB) maps ``/api/*`` onto the
    stage, but a client pointed directly at a bare ``*.execute-api.*`` host with no path
    would otherwise miss the stage and get a 403 on the first call. When the URL is a raw
    execute-api host with no path segment, append the default stage. URLs that already
    carry a path (fronted domains, or an execute-api URL with the stage included) are left
    unchanged.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return url
    if ".execute-api." in (parsed.netloc or "").lower() and parsed.path.strip("/") == "":
        # Rebuild from parsed components so the stage is inserted into the path segment,
        # never appended after a query string or fragment (e.g. a "…amazonaws.com?x=1"
        # base must become "…amazonaws.com/api?x=1", not "…amazonaws.com?x=1/api").
        return parsed._replace(path="/" + DEFAULT_API_STAGE).geturl()
    return url


# Route used to prove the stored API base URL actually reaches the deployed API. Requires a token, and
# that is the point -- see validate_api_gateway_reachable.
_STAGE_PROBE_PATH = "/secure-config"
_STAGE_PROBE_TIMEOUT_SECONDS = 15


def validate_api_gateway_reachable(api_gateway_url: str):
    """Check the stored API base URL reaches the deployed API, WITHOUT being authenticated yet.

    Returns ``(ok, detail)``. ``ok`` is False only for a definitive wrong-stage answer; a network
    failure returns True with a detail, because setup must not be blocked by a transient outage.

    Setup runs before login, so this cannot make an authenticated call. It does not need to: Amazon API
    Gateway reads the FIRST path segment as the stage name, and the two failure modes answer
    differently on an authenticated route requested with no token. Measured against a live deployment:

    | request                          | answer                                    |
    | -------------------------------- | ----------------------------------------- |
    | `<host>/api/secure-config`       | 401 `{"message":"Unauthorized"}`          |
    | `<host>/secure-config`           | 403 `{"message":"Forbidden"}`             |

    A **401** means the request reached the custom authorizer and was refused for having no token --
    which proves the stage segment is right and the route is deployed. A **403 Forbidden** means API
    Gateway rejected the request as naming a stage that does not exist, before any authorizer ran. So
    the absence of credentials is what makes this a clean probe rather than a limitation.

    Checked at setup rather than per command (owner question 89, NEW-LEAD-03 option A): the
    misconfiguration is created here, and a per-command pre-flight was already declined for its
    latency cost. Without it the first symptom is a 403 several token refreshes later, indistinguishable
    from a permission denial -- which is how this was twice mis-diagnosed as a permissions problem.
    """
    import requests

    probe_url = api_gateway_url.rstrip("/") + _STAGE_PROBE_PATH
    try:
        response = requests.get(probe_url, timeout=_STAGE_PROBE_TIMEOUT_SECONDS)
    except Exception as e:
        # Unreachable host, TLS failure, proxy. Not a stage problem, and not worth blocking setup for.
        return True, f"could not reach {probe_url} to verify the stage ({e})"

    if response.status_code == 403 and "forbidden" in (response.text or "").lower():
        return False, (
            f"{probe_url} answered 403 Forbidden. Amazon API Gateway reads the first path segment as "
            f"the deployment stage, so this URL names a stage that does not exist and no request will "
            f"reach a VAMS route -- every command would fail with a 403 that looks like a permission "
            f"denial. Supply the website URL, or the execute-api URL including its /{DEFAULT_API_STAGE} "
            f"stage path."
        )
    return True, f"{probe_url} answered {response.status_code}; the stage path resolves"


@click.command()
@click.argument('base_url')
@click.option('--force', '-f', is_flag=True, help='Force setup even if configuration exists')
@click.option('--skip-version-check', is_flag=True, help='Skip version mismatch confirmation prompts')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
def setup(ctx: click.Context, base_url: str, force: bool, skip_version_check: bool, json_output: bool):
    """
    Setup VamsCLI with VAMS base URL.
    
    This command configures VamsCLI to work with your VAMS deployment.
    It accepts any HTTP/HTTPS base URL (CloudFront, ALB, API Gateway, or custom domain),
    fetches the Amplify configuration, and extracts the API Gateway URL for storage.
    
    Examples:
        # Setup with CloudFront distribution
        vamscli setup https://d1234567890.cloudfront.net
        
        # Setup with custom domain
        vamscli setup https://vams.mycompany.com
        
        # Setup with ALB
        vamscli setup https://my-alb-123456789.us-west-2.elb.amazonaws.com
        
        # Setup with API Gateway directly
        vamscli setup https://abc123.execute-api.us-west-2.amazonaws.com
        
        # Setup specific profile
        vamscli --profile production setup https://prod-vams.example.com
        
        # Force overwrite existing configuration
        vamscli --profile dev setup https://dev-vams.example.com --force
        
        # JSON output mode
        vamscli setup https://vams.example.com --json-output
    """
    # Get profile manager from context
    profile_manager = get_profile_manager_from_context(ctx)
    
    # Check if configuration already exists
    if profile_manager.has_config() and not force:
        profile_name = profile_manager.profile_name
        message = f"Configuration already exists for profile '{profile_name}'. Use --force to overwrite."
        
        if json_output:
            result = {
                'status': 'skipped',
                'message': message,
                'profile': profile_name,
                'force_required': True
            }
            output_result(result, json_output)
        else:
            output_info(message, json_output)
        return
    
    # Validate URL format
    if not validate_base_url(base_url):
        error = click.BadParameter(
            "Invalid base URL. Please provide a valid HTTP/HTTPS URL."
        )
        output_error(error, json_output, error_type="Invalid URL")
        raise error
    
    # Ensure URL doesn't end with slash
    base_url = base_url.rstrip('/')

    # A raw execute-api URL must include the REST API stage path; a fronted (CloudFront/ALB)
    # or already-staged URL is returned unchanged.
    normalized_base_url = normalize_base_url_for_stage(base_url)
    if normalized_base_url != base_url:
        output_info(
            f"Detected a direct execute-api URL; using stage-inclusive base: {normalized_base_url}",
            json_output
        )
        base_url = normalized_base_url

    profile_name = profile_manager.profile_name

    # Status messages only in CLI mode
    output_status(f"Setting up VamsCLI with base URL: {base_url}", json_output)
    output_status(f"Profile: {profile_name}", json_output)
    
    try:
        # Create API client with base URL to fetch amplify config
        api_client = APIClient(base_url, profile_manager)
        
        # Check API version
        output_status("Checking API version...", json_output)
        version_info = api_client.check_version()
        
        if not version_info['match']:
            output_warning(
                "WARNING: Version mismatch detected:",
                json_output
            )
            output_info(f"   CLI version: {version_info['cli_version']}", json_output)
            output_info(f"   API version: {version_info['api_version']}", json_output)
            output_info("   This may cause compatibility issues.", json_output)
            
            if not skip_version_check and not json_output:
                if not click.confirm("Continue with setup?"):
                    output_info("Setup cancelled.", json_output)
                    return
            elif skip_version_check:
                output_info("   Skipping version check confirmation (--skip-version-check enabled)", json_output)
        else:
            output_status(
                f"✓ Version match: {version_info['cli_version']}", 
                json_output
            )
        
        # Fetch Amplify configuration
        output_status("Fetching Amplify configuration...", json_output)
        amplify_config = api_client.get_amplify_config()
        
        # Extract API Gateway URL from amplify config
        api_gateway_url = amplify_config.get('api')
        if not api_gateway_url:
            raise ConfigurationError(
                "No 'api' field found in amplify configuration response. "
                "Please verify the base URL points to a valid VAMS deployment."
            )
        
        # Validate extracted API Gateway URL
        if not validate_base_url(api_gateway_url):
            raise ConfigurationError(
                f"Invalid API Gateway URL extracted from amplify config: {api_gateway_url}"
            )
        
        # Ensure extracted API Gateway URL doesn't end with slash
        api_gateway_url = api_gateway_url.rstrip('/')

        # This is the value every later command builds its requests from, so it carries the stage
        # requirement rather than the bootstrap base_url normalized above. A raw execute-api host
        # reaches no deployed route without one: API Gateway reads the first path segment as the
        # stage name, so `<host>/database` is a request for a stage called "database" and is answered
        # 403 {"message": "Forbidden"} before any authorizer or handler runs. That is
        # indistinguishable from a permission denial, and `auth login` still succeeds because it talks
        # to Amazon Cognito directly and never reaches this URL.
        normalized_api_gateway_url = normalize_base_url_for_stage(api_gateway_url)
        if normalized_api_gateway_url != api_gateway_url:
            output_info(
                f"The deployment reported a stage-less API Gateway URL ({api_gateway_url}); "
                f"storing the stage-inclusive form instead: {normalized_api_gateway_url}",
                json_output
            )
            api_gateway_url = normalized_api_gateway_url

        output_status(f"✓ Extracted API Gateway URL: {api_gateway_url}", json_output)

        # Prove the URL about to be stored actually reaches the API, while the operator is still here
        # to correct it. Unauthenticated on purpose -- see validate_api_gateway_reachable.
        reachable, probe_detail = validate_api_gateway_reachable(api_gateway_url)
        if not reachable:
            raise ConfigurationError(probe_detail)
        output_status(f"✓ Verified API stage: {probe_detail}", json_output)
        
        # Wipe existing profile configuration if force is used
        if force:
            output_status("Removing existing configuration...", json_output)
            profile_manager.wipe_profile()
        
        # Save configuration with both base URL and extracted API Gateway URL
        from datetime import datetime, timezone
        config = {
            'base_url': base_url,
            'api_gateway_url': api_gateway_url,
            'amplify_config': amplify_config,
            'cli_version': get_version(),
            'setup_timestamp': datetime.now(timezone.utc).isoformat()
        }
        
        profile_manager.save_config(config)
        
        # Prepare result
        result = {
            'status': 'success',
            'message': 'Setup completed successfully!',
            'profile': profile_name,
            'base_url': base_url,
            'api_gateway_url': api_gateway_url,
            'cli_version': version_info['cli_version'],
            'api_version': version_info['api_version'],
            'version_match': version_info['match'],
            'config_path': str(profile_manager.config_dir)
        }
        
        def format_setup_result(data):
            """Format setup result for CLI display."""
            lines = []
            lines.append(f"Configuration saved to: {data['config_path']}")
            lines.append("\nNext steps:")
            if profile_name != 'default':
                lines.append(f"1. Run 'vamscli --profile {profile_name} auth login -u <username>' to authenticate")
                lines.append(f"2. Use 'vamscli --profile {profile_name} --help' to see available commands")
            else:
                lines.append("1. Run 'vamscli auth login -u <username>' to authenticate")
                lines.append("2. Use 'vamscli --help' to see available commands")
            return '\n'.join(lines)
        
        output_result(
            result,
            json_output,
            success_message="Setup completed successfully!",
            cli_formatter=format_setup_result
        )
        
    except ConfigurationError as e:
        # Only handle setup-specific business logic errors
        output_error(
            e,
            json_output,
            error_type="Configuration Error",
            helpful_message="Please verify the base URL points to a valid VAMS deployment."
        )
        raise click.ClickException(str(e))
