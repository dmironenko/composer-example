# Composer example

A runnable GitOps repository for [Composer](https://github.com/dmironenko/composer).
It deploys an API built from source, a web app, and nginx with Certbot. Separate
nginx instances route prod and stage traffic; a shared ingress selects the
environment by cookie.

## Quick start

Requires Go 1.26.8 or newer, Git, and a running Docker Engine or Docker Desktop.
Install Composer from `master` and ensure `$HOME/go/bin` is on your `PATH`:
The example uses changes not yet included in v1.0.0.

```sh
go install github.com/dmironenko/composer/cmd/composer@master
```

Run these commands in a new directory:

```sh
git clone --branch master https://github.com/dmironenko/composer-example.git
cd composer-example
git branch dev master
cp host.yaml.example ../composer.local.yaml
install -m 600 demo.agekey ../composer-example.agekey
composer apply --config ../composer.local.yaml --wait --timeout 15m
```

The first build downloads and compiles the API. The ingress creates a local TLS
certificate and listens on `127.0.0.1:18080` and `127.0.0.1:18443`.
Check that these ports are free before deploying. For another copy, change
`installation` and `state_dir` in the host YAML, and change the published ports
and `app.tls.https_port` in `apps/ingress/nginx/vars/base.yaml`. Commit app changes.

Check both environments, then start watching for commits:

```sh
curl -ki https://localhost:18443/api/
curl -ki --cookie 'composer_stage=composer-example-test:stage' https://localhost:18443/api/
composer run --config ../composer.local.yaml
```

The responses contain `X-Composer-Environment: prod` and `stage`, respectively.
`-k` accepts the local development certificate. Ctrl-C stops Composer; containers
keep running. Host paths are relative to `composer.local.yaml`.
The example polls local Git every two seconds; use `source.poll_interval: 1m`
when switching to a remote repository.

The age key and `composer-example-test` token are public demo values. Use your
own key and token before deploying a real service.

## Deploy a change

Keep Composer running and use another terminal in this repository:

```sh
git switch dev
# edit apps/demo/api/vars/base.yaml.
git add apps/demo/api/vars/base.yaml
git commit -m 'feat: update API settings'
```

Check the stage URL, then promote the change:

```sh
git switch master
git merge dev
```

Local mode reads commits without a push. Remote mode requires a push.
Config-only changes also deploy. Revert the commit to roll back.
For another API version, change `build.tag`; Docker builds it locally and keeps
the image for reuse. The shared ingress follows `master`; test the inner nginx
on `dev`.

## Repository layout

```text
apps/
  demo/
    api/                   # prod + stage; custom Dockerfile and message config
    web/                   # prod only
    nginx/                 # per-environment routing
  ingress/
    nginx/                 # shared TLS ingress and encrypted routing token
host.yaml.example          # copy outside Git
demo.agekey                # public demo key
```

`base.yaml` holds shared settings. Every environment except `prod` also reads
`stage.yaml`; without that file the app does not run there. This is why `/api/`
works in stage but `/` returns an error: stage has no web app. Use `app` for app
settings, `compose` for Compose settings, and `secrets` for encrypted values.

Each environment has its own namespace network. Only the ingress publishes host
ports; app services can use `deploy.replicas`. Composer's built-in template
reads `compose.x-composer.route_namespace` to connect ingress to the demo networks.
Service names are automatic: `nginx` in prod, `nginx-stage` in stage, and
`nginx-dan` in a personal environment named `dan`. The template also supplies
restart and log rotation defaults; no copied Compose template is needed.

See [the app guide](https://github.com/dmironenko/composer/blob/master/docs/apps.md)
to add, configure, scale, or remove an app.

## Personal environments

Create a branch:

```sh
git branch users/alice dev
```

Add it to `source.branches` in the external host YAML:

```yaml
source:
  branches:
    prod: master
    stage: dev
    alice: users/alice
```

Composer reloads the config on its next pass. Select the environment with
`composer_stage=composer-example-test:alice`. It uses its branch's `base.yaml`
and `stage.yaml`, with separate networks and volumes; prod data is not copied.
Removing the mapping removes its containers and retains volumes/images.
Adding or removing environments can briefly interrupt ingress as networks change.

Missing or invalid cookies select prod. A valid token selecting an unavailable
or unknown environment returns an error. The routing cookie does not authenticate
users. Use a separate browser profile for stage to separate cookies and local storage.

## Builds and secrets

The API's `Dockerfile.tmpl` clones [http-echo](https://github.com/hashicorp/http-echo)
and checks out `.Vars.build.tag` from `.Vars.build.repository`. A Go builder stage
compiles it; the final BusyBox image contains the binary and license.

Change `build.tag` or the Dockerfile on `dev`, then merge into `master`.
Matching build inputs reuse an image; config-only changes do not rebuild it.
A tag, SHA, or branch is accepted, but moving the same tag or branch does not
trigger a rebuild. Composer passes an empty build context and does not forward
Git credentials. See [source builds](https://github.com/dmironenko/composer/blob/master/docs/builds.md).

`secrets.stage_token` in `apps/ingress/nginx/vars/base.yaml` is an individually
encrypted age value. The host's `age_key_file` decrypts it for `.Secrets.stage_token`.
For a demo, encrypt a replacement and paste the output into that field:

```sh
printf '%s' 'another-demo-token' | composer secrets encrypt --key-file ../composer-example.agekey
```

Commit the change and use the new token in your routing cookie. For real secrets,
generate a private key outside Git, update `age_key_file`, and encrypt every
secret with that key. Follow the [secrets guide](https://github.com/dmironenko/composer/blob/master/docs/secrets.md).

## Deploy on a server

Configure a [remote Git source](https://github.com/dmironenko/composer/blob/master/docs/configuration.md#remote-git-and-authentication).
For public TLS, update `apps/ingress/nginx/vars/base.yaml`:

- Set `app.tls.domain` to your domain and `app.tls.https_port` to `443`.
- Set `compose.services.nginx.environment.CERTBOT_EMAIL` to your email.
- Set `compose.services.nginx.environment.USE_LOCAL_CA` to `"0"`.
- Set `compose.services.nginx.ports` to `[80:80, 443:443]`.

Point DNS to the server and open port 80 for HTTP-01 and 443 for HTTPS.
The HTTP redirect excludes `/.well-known/acme-challenge/` so renewal still works.
The inner nginx keeps redirects relative and forwards the original host and
HTTPS scheme; internal container ports must not appear in browser redirects. Use a new
empty certificate volume when switching from the local CA to public ACME.
The [nginx-certbot image](https://github.com/JonasAlfredsson/docker-nginx-certbot)
renews certificates and reloads nginx. Certificates persist in a named volume.

On Ubuntu, use [composer install](https://github.com/dmironenko/composer/blob/master/docs/systemd.md)
to run the agent through systemd.

## Contributing

Write documentation in English. Use two-space YAML indentation, single-line
`command` and `healthcheck.test` strings, and a blank line before `compose` and
`secrets`. Shell commands must explicitly use `sh -c`. Keep demo values public.

Commit changes before running `composer validate --config ../composer.local.yaml`.
Test deployments in a separate installation with its own state directory and
free ports. Describe your checks in the pull request.
