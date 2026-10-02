# Полная приёмка

Сценарий дополняет обычный GitOps-пример PostgreSQL и исходниками API. Его
запускают в **одноразовой Ubuntu VM**. `prepare.py` создаёт отдельную копию,
новые age/SSH identities и зашифрованные fixtures; рабочий checkout не меняет.
API использует PostgreSQL client и BusyBox HTTP server из закреплённых images.
Он читает начальную запись из БД перед запуском HTTP. Это тестовое приложение.

На macOS нужны Lima, Git, SOPS, age, Python с PyYAML и Go. Из composer-example:

```sh
python3 -m venv .venv
.venv/bin/pip install -r scripts/requirements.txt
.venv/bin/python test/full/prepare.py /tmp/composer-full
limactl start --name=composer-acceptance --tty=false test/full/ubuntu.yaml
limactl shell composer-acceptance sudo bash -c \
  'apt-get update && apt-get install -y docker.io git python3-yaml openssh-server'
(cd ../composer && CGO_ENABLED=0 GOOS=linux GOARCH=arm64 go build -o /tmp/composer-acceptance-linux ./cmd/composer)
COPYFILE_DISABLE=1 tar -C /tmp/composer-full -cf - . | \
  limactl shell composer-acceptance sudo bash -c \
  'install -d -m 700 /var/tmp/composer-acceptance && tar -xf - -C /var/tmp/composer-acceptance'
limactl copy /tmp/composer-acceptance-linux composer-acceptance:/tmp/composer
limactl shell composer-acceptance sudo bash -c \
  'install /tmp/composer /usr/local/bin/composer && bash /var/tmp/composer-acceptance/repo/test/full/serve-git.sh'
```

Для Intel используйте `GOARCH=amd64`. VM не монтирует каталоги хоста и не
публикует порты приложений. Отдельный SSH daemon слушает только loopback VM,
предоставляя настоящие Git-over-SSH repositories для source checkout и polling.
Ключи лежат вне GitOps. На VM не устанавливаются SOPS, age, Go или buildx.

Убедитесь, что `docker ps -aq` и `docker image ls -q` в VM пусты. Вся система:

```sh
limactl shell composer-acceptance sudo composer apply \
  --config /var/tmp/composer-acceptance/host.yaml --wait --timeout 15m
```

Никаких предварительных build, pull, создания сетей, volumes, схемы БД или
сертификата не требуется. `apply --wait` повторяет неудачные projects, поэтому
приложение, стартовавшее раньше зависимости, может дождаться следующего прохода.
Затем:

```sh
limactl shell composer-acceptance sudo python3 \
  /var/tmp/composer-acceptance/repo/test/full/check.py \
  /usr/local/bin/composer /var/tmp/composer-acceptance/host.yaml
```

Проверки: общие images prod/stage, зашифрованный DB password с `$`, начальная
схема и отдельные данные окружений; существующий `verify.py` (routing, scale,
config-only, изоляция); повторное шифрование без rollout; secret-only rotation;
отдельный stage SHA; promotion и Git revert без доступного checkout credential;
config-only с прежним image; ошибка build и независимая выкатка соседа;
частичная runtime failure и восстановление через revert; настоящий SSH fetch
в `composer run`, применение commit/revert, сохранение контейнеров после остановки.
`last-apply.log` и `run.log` остаются внутри закрытого каталога fixture.

На том же стенде можно повторить настоящий HTTP-01 и renew через тестовый CA:

```sh
limactl shell composer-acceptance sudo python3 /var/tmp/composer-acceptance/repo/test/acme.py /usr/local/bin/composer /var/tmp/composer-acceptance/host.yaml
```

После сценариев установите агент, сохраните checkpoint и перезапустите VM:

```sh
limactl shell composer-acceptance sudo composer install --config /var/tmp/composer-acceptance/remote.yaml
limactl shell composer-acceptance sudo python3 /var/tmp/composer-acceptance/repo/test/full/check.py /usr/local/bin/composer /var/tmp/composer-acceptance/host.yaml checkpoint
limactl stop composer-acceptance
limactl start --tty=false composer-acceptance
limactl shell composer-acceptance sudo python3 /var/tmp/composer-acceptance/repo/test/full/check.py /usr/local/bin/composer /var/tmp/composer-acceptance/host.yaml reboot-check
```

Проверяется новый boot ID, работа systemd, прежние container IDs, запись БД и
доступность HTTPS, затем обработка нового commit/revert агентом после reboot.
После приёмки удалите только эту VM через
`limactl delete --force composer-acceptance` и созданный `/tmp/composer-full`,
содержащий тестовые приватные ключи. Для повторного cold-start нужна новая VM.
Публичный ACME/DNS проверяется отдельно с реальным доменом.

## macOS: локальный бинарник

После копирования fixture в VM её SSH-сервер можно использовать только для
исходников приложения. GitOps и Docker для этой проверки находятся на macOS.
Оставьте VM включённой и пробросьте её loopback SSH:

```sh
ssh -F ~/.lima/composer-acceptance/ssh.config -N \
  -L 127.0.0.1:22222:127.0.0.1:22222 -o ExitOnForwardFailure=yes lima-composer-acceptance
```

В другом терминале, из composer-example, измените **локальные** host files
уже подготовленного `/tmp/composer-full` (копия в VM остаётся прежней):

```sh
.venv/bin/python - <<'PY'
from pathlib import Path
import yaml
root = Path('/tmp/composer-full')
host = yaml.safe_load((root / 'host.yaml').read_text())
host['installation'] = 'full-macos'
host['docker'] = {'context': 'desktop-linux'}
for name in ('host.yaml', 'remote.yaml'):
    (root / name).write_text(yaml.safe_dump(host))
PY
(cd ../composer && go build -o bin/composer ./cmd/composer)
../composer/bin/composer apply --config /tmp/composer-full/host.yaml --wait --timeout 15m
.venv/bin/python test/full/check.py ../composer/bin/composer /tmp/composer-full/host.yaml
```

Имя `remote.yaml` здесь осталось именем файла сценария; его `source.mode: local`
проверяет требуемый цикл локальный commit → `composer run` → HTTP response.
На Ubuntu тот же шаг использует настоящий SSH remote. Нужны свободные host
порты 18080/18443 и 22222. Проверка macOS выполняет те же application сценарии,
кроме systemd/reboot; Docker Desktop уже установлен, его хранилище не очищается.
После неё удалите только тестовые Docker resources с project prefix `full-macos-`
и закройте SSH forward; не применяйте общий prune на рабочем Docker.
