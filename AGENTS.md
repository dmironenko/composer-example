# Composer example

This is a runnable GitOps example for Composer, not deployment state.

- Check Git status before edits and preserve existing work.
- Use two-space YAML indentation and single-line command/healthcheck.test strings.
- Put a blank line before compose and secrets blocks. Use sh -c for shell commands.
- Keep secrets last in vars files, before any SOPS metadata.
- Keep public demo values in Git. Never commit real credentials, private keys, or rendered state.
- Keep host configuration outside this repository; host.yaml.example is a template.
- One app directory is one Compose project; each namespace/environment has its own network.
- Prod reads base.yaml; every other environment reads base.yaml plus stage.yaml.
- Missing stage.yaml disables an app outside prod. Keep shared settings in base.yaml.
- Use pinned image digests. Only apps/ingress/nginx publishes ports; scalable apps have no container_name.
- Validate changes with Composer. Try deployments in a disposable copy using a unique
  installation, separate state directory, and free ports.
- Keep this repository focused on the runnable example; do not add test infrastructure.
