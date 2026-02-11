#!/usr/bin/env python3
"""
VAMS Permission Template Applicator

Reads JSON permission templates and uses vamscli CLI commands to create
or delete roles and import constraints via the template import API.

Templates use {{VARIABLE}} placeholders that are substituted server-side.
Variable values are provided via --variables (JSON string), --variables-file
(JSON file), or --var (KEY=VALUE pairs). ROLE_NAME is always set via the
dedicated --role-name parameter.

Usage:
    # Create a database admin role
    python apply_template.py \
        --template documentation/permissionsTemplates/database-admin.json \
        --role-name my-db-admin \
        --variables '{"DATABASE_ID": "my-db"}'

    # Using a variables file
    python apply_template.py \
        --template documentation/permissionsTemplates/database-admin.json \
        --role-name my-db-admin \
        --variables-file vars.json

    # Using individual --var flags
    python apply_template.py \
        --template documentation/permissionsTemplates/deny-tagged-assets.json \
        --role-name my-db-admin \
        --var TAG_VALUE=locked

    # Dry run
    python apply_template.py \
        --template documentation/permissionsTemplates/database-user.json \
        --role-name my-db-user \
        --variables '{"DATABASE_ID": "my-db"}' --dry-run

    # Delete
    python apply_template.py \
        --template documentation/permissionsTemplates/database-admin.json \
        --role-name my-db-admin --delete
"""

import argparse
import json
import subprocess
import sys
import os


def load_template(template_path):
    """Load a JSON permission template file and return the parsed dict."""
    try:
        with open(template_path, encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON in template file '{template_path}': {e}")
        sys.exit(1)

    if not isinstance(data, dict):
        print(f"ERROR: Template file must contain a JSON object (got {type(data).__name__})")
        sys.exit(1)

    if "constraints" not in data:
        print(f"ERROR: Template file missing required 'constraints' key")
        sys.exit(1)

    return data


def import_constraints(template_data, profile=None, dry_run=False):
    """Import constraints using vamscli role constraint template import.

    In dry-run mode, prints a summary of what would be imported.
    Returns True on success, False on failure.
    """
    metadata = template_data.get("metadata", {})
    template_name = metadata.get("name", "Unknown")
    constraints = template_data.get("constraints", [])
    variable_values = template_data.get("variableValues", {})
    role_name = variable_values.get("ROLE_NAME", "unknown")

    if dry_run:
        print(f"\n--- Constraint Import (DRY RUN) ---")
        print(f"  Template: {template_name}")
        print(f"  Role: {role_name}")
        print(f"  Variables: {json.dumps(variable_values)}")
        print(f"  Constraints to import: {len(constraints)}")
        for i, c in enumerate(constraints, 1):
            name = c.get("name", "unnamed")
            obj_type = c.get("objectType", "unknown")
            perms = c.get("groupPermissions", [])
            perm_summary = ", ".join(
                f"{p.get('action', '?')}({p.get('type', '?')})" for p in perms
            )
            criteria_and = len(c.get("criteriaAnd", []))
            criteria_or = len(c.get("criteriaOr", []))
            criteria_parts = []
            if criteria_and > 0:
                criteria_parts.append(f"AND:{criteria_and}")
            if criteria_or > 0:
                criteria_parts.append(f"OR:{criteria_or}")
            criteria_str = " ".join(criteria_parts) if criteria_parts else "none"
            print(f"    [{i}] {name} | {obj_type} | {perm_summary} | criteria: {criteria_str}")
        print(f"  [DRY RUN] vamscli role constraint template import -j <template_json>")
        return True

    # Serialize and send via CLI
    template_json = json.dumps(template_data)
    args = ["role", "constraint", "template", "import", "-j", template_json, "--json-output"]
    result = run_vamscli(args, profile=profile)

    if result.returncode == 0:
        # Parse response for summary
        try:
            response = json.loads(result.stdout)
            count = response.get("constraintsCreated", len(constraints))
            msg = response.get("message", "Import successful")
            print(f"    {msg}")
            print(f"    Constraints created: {count}")
        except (json.JSONDecodeError, AttributeError):
            print(f"    Import completed successfully.")
            if result.stdout:
                print(f"    {result.stdout.strip()}")
        return True
    else:
        stderr = result.stderr.strip() if result.stderr else ""
        stdout = result.stdout.strip() if result.stdout else ""
        print(f"    ERROR importing constraints: {stderr or stdout}")
        return False


def run_vamscli(args, profile=None, capture_output=True):
    """Run a vamscli command and return the result."""
    cmd = ["vamscli"]
    if profile:
        cmd.extend(["--profile", profile])
    cmd.extend(args)

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        cmd,
        capture_output=capture_output,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        env=env,
    )
    return result


def create_role(role_name, description, mfa_required, profile=None, dry_run=False):
    """Create a role using vamscli."""
    print(f"  Creating role: {role_name}")
    if dry_run:
        print(f"    [DRY RUN] vamscli role create -r {role_name} --description \"{description}\"")
        return True

    args = ["role", "create", "-r", role_name, "--description", description]
    if mfa_required:
        args.append("--mfa-required")

    result = run_vamscli(args, profile=profile)
    if result.returncode == 0:
        print(f"    Role '{role_name}' created successfully.")
        return True
    else:
        stderr = result.stderr.strip() if result.stderr else ""
        if "already exists" in stderr.lower() or "already exists" in (result.stdout or "").lower():
            print(f"    Role '{role_name}' already exists, continuing.")
            return True
        print(f"    ERROR creating role: {stderr or result.stdout}")
        return False


def delete_role(role_name, profile=None, dry_run=False):
    """Delete a role using vamscli."""
    print(f"  Deleting role: {role_name}")
    if dry_run:
        print(f"    [DRY RUN] vamscli role delete -r {role_name} --confirm")
        return True

    args = ["role", "delete", "-r", role_name, "--confirm", "--json-output"]
    result = run_vamscli(args, profile=profile)
    if result.returncode == 0:
        print(f"    Role '{role_name}' deleted.")
        return True
    else:
        stderr = result.stderr.strip() if result.stderr else ""
        print(f"    WARNING deleting role: {stderr or result.stdout}")
        return False


def assign_user_to_role(user_id, role_name, profile=None, dry_run=False):
    """Assign a user to a role using vamscli."""
    print(f"  Assigning user '{user_id}' to role '{role_name}'")
    if dry_run:
        print(f"    [DRY RUN] vamscli role user create -u {user_id} --role-name {role_name}")
        return True

    args = ["role", "user", "create", "-u", user_id, "--role-name", role_name]
    result = run_vamscli(args, profile=profile)
    if result.returncode == 0:
        print(f"    User '{user_id}' assigned to '{role_name}'.")
        return True
    else:
        stderr = result.stderr.strip() if result.stderr else ""
        print(f"    WARNING assigning user: {stderr or result.stdout}")
        return False


def resolve_variables(args):
    """Build the variable values dict from all input sources.

    Priority (later overrides earlier):
      1. --variables-file (JSON file)
      2. --variables (JSON string)
      3. --var KEY=VALUE (individual overrides)
      4. ROLE_NAME always set from --role-name
    """
    var_values = {}

    # 1. Load from JSON file
    if args.variables_file:
        if not os.path.isfile(args.variables_file):
            print(f"ERROR: Variables file not found: {args.variables_file}")
            sys.exit(1)
        try:
            with open(args.variables_file, encoding="utf-8") as f:
                file_vars = json.load(f)
        except json.JSONDecodeError as e:
            print(f"ERROR: Invalid JSON in variables file '{args.variables_file}': {e}")
            sys.exit(1)
        if not isinstance(file_vars, dict):
            print(f"ERROR: Variables file must contain a JSON object (got {type(file_vars).__name__})")
            sys.exit(1)
        var_values.update(file_vars)

    # 2. Load from JSON string
    if args.variables:
        try:
            json_vars = json.loads(args.variables)
        except json.JSONDecodeError as e:
            print(f"ERROR: Invalid JSON in --variables: {e}")
            sys.exit(1)
        if not isinstance(json_vars, dict):
            print(f"ERROR: --variables must be a JSON object (got {type(json_vars).__name__})")
            sys.exit(1)
        var_values.update(json_vars)

    # 3. Apply individual --var overrides
    for var_str in args.var:
        if "=" not in var_str:
            print(f"ERROR: --var must be in KEY=VALUE format: '{var_str}'")
            sys.exit(1)
        key, value = var_str.split("=", 1)
        var_values[key] = value

    # 4. Handle ROLE_NAME: --role-name is authoritative
    if "ROLE_NAME" in var_values and var_values["ROLE_NAME"] != args.role_name:
        print(
            f"ERROR: ROLE_NAME in variables ('{var_values['ROLE_NAME']}') "
            f"conflicts with --role-name ('{args.role_name}').\n"
            f"  The --role-name parameter is the authoritative source for ROLE_NAME.\n"
            f"  Either remove ROLE_NAME from your variables input or make it match --role-name."
        )
        sys.exit(1)
    var_values["ROLE_NAME"] = args.role_name

    return var_values


def apply_template(template_path, var_values, profile=None, dry_run=False,
                   delete=False, assign_user=None, role_description=None,
                   mfa_required=False):
    """Main function to apply (or delete) a permission template.

    Workflow:
      1. Load JSON template
      2. Inject variableValues into template data
      3. DELETE mode: delete the role (constraints are managed by the API)
      4. CREATE mode: create role, then import constraints via the API
      5. Optionally assign user
    """
    role_name = var_values["ROLE_NAME"]

    # Load template
    print(f"\nLoading template: {template_path}")
    template_data = load_template(template_path)

    metadata = template_data.get("metadata", {})
    template_name = metadata.get("name", "Unknown")
    template_version = metadata.get("version", "1.0")
    constraints = template_data.get("constraints", [])

    # Inject variableValues into the template
    template_data["variableValues"] = var_values

    # Determine role description
    if not role_description:
        role_description = f"{template_name} - {role_name}"

    print(f"Template: {template_name} v{template_version}")
    print(f"Role: {role_name}")
    print(f"Variables: {var_values}")
    if dry_run:
        print("MODE: DRY RUN (no changes will be made)")
    elif delete:
        print("MODE: DELETE")
    else:
        print("MODE: CREATE")
    print()

    if delete:
        # Delete mode: delete the role only.
        # Note: constraint IDs are server-generated UUIDs, so individual constraint
        # deletion by name is no longer practical. Delete the role and recreate if
        # needed. Constraints that reference the deleted role's groupId will no
        # longer match any users but are not automatically removed. Use the VAMS UI
        # or vamscli role constraint commands to clean up orphaned constraints.
        print("--- Deleting Role ---")
        print("  Note: Constraints with server-generated IDs are not automatically")
        print("  deleted. Use the VAMS UI or 'vamscli role constraint' commands")
        print("  to remove orphaned constraints if needed.")
        delete_role(role_name, profile=profile, dry_run=dry_run)
        print("\nDeletion complete.")
        return

    # Create mode
    print("--- Creating Role ---")
    role_success = create_role(
        role_name,
        role_description,
        mfa_required,
        profile=profile,
        dry_run=dry_run,
    )
    if not role_success and not dry_run:
        print("FATAL: Could not create role. Aborting.")
        sys.exit(1)

    # Import constraints via the template import API
    print(f"\n--- Importing Constraints ({len(constraints)} total) ---")
    import_success = import_constraints(template_data, profile=profile, dry_run=dry_run)

    if not import_success:
        print("\nWARNING: Constraint import failed.")
    else:
        print(f"\nConstraint import completed.")

    # Assign user if requested
    if assign_user:
        print(f"\n--- Assigning User ---")
        assign_user_to_role(assign_user, role_name, profile=profile, dry_run=dry_run)

    # Summary
    print(f"\n{'='*60}")
    print(f"Template: {template_name}")
    print(f"Role: {role_name}")
    print(f"Constraints: {len(constraints)}")
    if dry_run:
        print("Status: DRY RUN complete (no changes made)")
    else:
        print("Status: Complete")
    print(f"{'='*60}")


def main():
    parser = argparse.ArgumentParser(
        description="Apply VAMS permission templates using vamscli",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Variable Input:
  Templates define their required variables in the JSON "variables" array.
  Provide variable values using any combination of these methods
  (later sources override earlier ones):

    1. --variables-file vars.json       (JSON file)
    2. --variables '{"KEY": "VALUE"}'   (JSON string)
    3. --var KEY=VALUE                  (individual, repeatable)

  ROLE_NAME is always set from --role-name and cannot be overridden.

Examples:
  # Create a database admin role
  python apply_template.py \\
      --template documentation/permissionsTemplates/database-admin.json \\
      --role-name my-db-admin \\
      --variables '{"DATABASE_ID": "my-db"}'

  # Using a variables file
  python apply_template.py \\
      --template documentation/permissionsTemplates/database-admin.json \\
      --role-name my-db-admin \\
      --variables-file vars.json

  # Using individual --var flags
  python apply_template.py \\
      --template documentation/permissionsTemplates/deny-tagged-assets.json \\
      --role-name my-db-admin \\
      --var TAG_VALUE=locked

  # Preview (dry run)
  python apply_template.py \\
      --template documentation/permissionsTemplates/database-user.json \\
      --role-name my-db-user \\
      --variables '{"DATABASE_ID": "my-db"}' --dry-run

  # Delete a role
  python apply_template.py \\
      --template documentation/permissionsTemplates/database-admin.json \\
      --role-name my-db-admin --delete

  # Create and assign a user
  python apply_template.py \\
      --template documentation/permissionsTemplates/database-user.json \\
      --role-name my-db-user \\
      --variables '{"DATABASE_ID": "my-db"}' \\
      --assign-user john.doe

  # Custom role description and MFA
  python apply_template.py \\
      --template documentation/permissionsTemplates/database-admin.json \\
      --role-name my-secure-admin \\
      --variables '{"DATABASE_ID": "my-db"}' \\
      --role-description "Secure admin for my-db" --mfa-required

See documentation/PermissionsGuide.md for template details and constraint design.
        """,
    )

    parser.add_argument(
        "--template", "-t", required=True,
        help="Path to JSON permission template file",
    )
    parser.add_argument(
        "--role-name", "-r", required=True,
        help="Role name to create/manage. Sets ROLE_NAME variable.",
    )
    parser.add_argument(
        "--role-description",
        help="Role description (default: template name + role name)",
    )
    parser.add_argument(
        "--mfa-required", action="store_true",
        help="Require MFA for the role",
    )
    parser.add_argument(
        "--variables", "-V",
        help="JSON string of template variables, e.g. '{\"DATABASE_ID\": \"my-db\"}'",
    )
    parser.add_argument(
        "--variables-file", "-f",
        help="Path to a JSON file containing template variables",
    )
    parser.add_argument(
        "--var", "-v", action="append", default=[],
        help="Individual variable in KEY=VALUE format (repeatable, overrides --variables)",
    )
    parser.add_argument(
        "--profile", "-p",
        help="vamscli profile to use",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview changes without executing",
    )
    parser.add_argument(
        "--delete", action="store_true",
        help="Delete the role (constraints must be cleaned up separately)",
    )
    parser.add_argument(
        "--assign-user",
        help="Also assign this user ID to the role after creation",
    )

    args = parser.parse_args()

    # Validate template exists
    if not os.path.isfile(args.template):
        print(f"ERROR: Template file not found: {args.template}")
        sys.exit(1)

    # Resolve variables from all input sources
    var_values = resolve_variables(args)

    apply_template(
        template_path=args.template,
        var_values=var_values,
        profile=args.profile,
        dry_run=args.dry_run,
        delete=args.delete,
        assign_user=args.assign_user,
        role_description=args.role_description,
        mfa_required=args.mfa_required,
    )


if __name__ == "__main__":
    main()
