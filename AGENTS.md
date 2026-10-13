# Composer example

This is a runnable GitOps example for Composer, not deployment state.

- Check Git status before edits and preserve existing work.
- Write all documentation and comments in English. Use the installed `composer` command in guides.
- Quote YAML values only when needed to preserve their type or meaning.
- Use two-space YAML indentation and single-line command/healthcheck.test strings.
- Put a blank line before compose blocks. Use sh -c for shell commands.
- Use only build, app, and compose at the top level, in that order; omit unused blocks.
- Keep application settings and secrets under app in the service's native shape;
  render YAML/JSON directly with toYAML/toJSON instead of rebuilding the config in templates.
- Put environment variables with their original names under app.env. Render an env
  file and reference it through compose.services.<service>.env_file.
- Docker settings belong in compose; Composer options, including enabled, belong
  in compose.x-composer. The built-in Compose template does not read app.
- Keep application secrets under app alongside ordinary settings as encrypted ENC[<base64>] values.
- demo.agekey is an intentionally public demo identity; never use it for real secrets.
- Keep real credentials, private keys, and rendered state out of Git.
- Use composer secrets encrypt/decrypt with the shared age key.
- Keep host configuration outside this repository; host.yaml.example is a template.
- One app directory is one Compose project; all apps in an installation share a network.
- Prod reads base.yaml; every other environment reads base.yaml plus stage.yaml.
- Missing stage.yaml disables an app outside prod. Keep shared settings in base.yaml.
- Use image tags without digests. Only apps/ingress/nginx publishes ports; scalable apps have no container_name.
- Validate changes with Composer. Try deployments in a disposable copy using a unique
  installation, separate state directory, and free ports.
- Keep this repository focused on the runnable example; do not add test infrastructure.
