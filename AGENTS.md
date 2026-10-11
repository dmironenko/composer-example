# Composer example

This is a runnable GitOps example for Composer, not deployment state.

- Check Git status before edits and preserve existing work.
- Use two-space YAML indentation and single-line command/healthcheck.test strings.
- Put a blank line before compose and secrets blocks. Use sh -c for shell commands.
- Keep public demo values in Git. Never commit real credentials, private keys, or rendered state.
- Keep host configuration outside this repository; host.yaml.example is a template.
- One app directory is one Compose project; each namespace/environment has its own network.
- Prod reads base.yaml; every other environment reads base.yaml plus stage.yaml.
- Missing stage.yaml disables an app outside prod. Keep shared settings in base.yaml.
- Use pinned image digests. Only the gateway publishes ports; scalable apps have no container_name.
- Validate changes with Composer. Run Docker acceptance tests only in a disposable copy,
  using a unique installation, separate state directory, and free ports.
- Do not commit or switch branches in a live watched checkout just to run tests.
