# Приёмка GitOps-примера

Сценарии запускаются в одноразовой копии GitOps на Linux или macOS.
Для параллельного запуска задайте уникальную installation и свободные gateway
ports в vars/prod.yaml. Передайте HTTPS-порт через --https-port в оба сценария.
Они делают commits/reverts в копии GitOps, выключают stage nginx, масштабируют API
и перезапускают тестовый внешний nginx. `acme.py` оставляет тестовый override и CA-файл
в копии репозитория; для повторного полного прогона используйте свежую VM.

Нужны Git, Python 3.11+ с PyYAML, Docker, доступ в registry и Linux-бинарник Composer.
На Ubuntu 24.04 пакеты можно установить так:

```sh
sudo apt-get update
sudo apt-get install -y docker.io git python3-yaml
```

Скопируйте репозиторий без `.git`/`.venv` и приватный age key в VM отдельно.
Например, `/var/tmp/composer-check/repo` и `/var/tmp/composer-check/age.txt`.
Ключ должен соответствовать зашифрованному vars/base.yaml; первоначальная
настройка описана в [README](../README.md). Каталог проверки закройте режимом
0700, ключ — 0600. Host YAML `/var/tmp/composer-check/host.yaml`:

```yaml
version: 1
installation: ingress-check
source:
  mode: local
  path: repo
  branch: master
  poll_interval: 2s
state_dir: state
age_key_file: age.txt
environments: [prod, stage]
docker:
  host: unix:///var/run/docker.sock
```

В копии репозитория выполните `git init -b master`, настройте локальные
`user.name`/`user.email` для тестовых commits и сделайте первый commit.
Запускайте команды от пользователя с доступом к Docker, ключу и state_dir.
Из `/var/tmp/composer-check`:

```sh
composer apply --config host.yaml --wait --timeout 15m
python3 repo/test/verify.py /usr/local/bin/composer host.yaml
python3 repo/test/acme.py /usr/local/bin/composer host.yaml
```

Если бинарник находится в другом месте, замените путь. Первый apply на пустом
Docker проверяет создание всех сетей, volumes и сертификата одной командой.

`verify.py` проверяет:

- prod/stage по cookie и ошибку для отсутствующего stage web;
- повторный apply без перезапусков;
- изменение stage nginx config без изменений соседних контейнеров;
- API replicas 1 → 3 → 1 и запросы ко всем трём репликам через nginx;
- config-only rollout и Git revert с проверкой HTTP-ответа;
- недоступный stage nginx без перехода в prod, разные сети окружений.

`acme.py` использует закреплённый образ [Pebble](https://github.com/letsencrypt/pebble),
его настоящую HTTP-01 валидацию через внешний nginx и отдельный тестовый CA. По умолчанию используются 18080/18443. CA подключается к сети edge, management
порт выделяется Docker автоматически на loopback;
системное хранилище доверия не меняется. Проверяется доверенная цепочка и hostname,
новый fingerprint после принудительного renew, прежние ID/StartedAt контейнеров
и сохранение сертификата после перезапуска внешний nginx.

Обновлённые сценарии nginx проверяются локально на Docker Desktop. Публичный ACME,
реальный DNS и firewall сервера проверяются отдельно при подключении домена.
После проверки удалите VM целиком; рабочие серверы и их Docker не используются.

Полный сценарий с source builds, PostgreSQL, secrets, SSH polling и перезагрузкой
находится в [full/README.md](full/README.md). Он готовит собственные тестовые ключи
и копию примера, затем поднимает весь стенд одной командой на пустом Docker.
