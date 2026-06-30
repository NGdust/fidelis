# Публикация `fidelis` на PyPI

Пошаговая инструкция по сборке и выкладке пакета. Аккаунт на PyPI уже есть.

Имя `fidelis` на PyPI **свободно** (проверено). Все артефакты проходят
`twine check`.

---

## 0. Требования к инструментам

```bash
python3 -m pip install --upgrade build twine
```

> Важно: пакет использует современные метаданные (PEP 639, `Metadata-Version 2.4`,
> поле `License-Expression`). Нужен **twine ≥ 6.1** и **setuptools ≥ 77** —
> более старые версии не распарсят метаданные. Локально проверено на twine 6.2.0.

---

## 1. Одноразовая настройка: API-токен

1. Зайди на <https://pypi.org/manage/account/token/> и создай **API token**
   (scope сначала можно «Entire account»; после первой заливки пересоздай токен
   со scope только для проекта `fidelis`).
2. Сохрани токен в `~/.pypirc` (права `chmod 600`):

   ```ini
   [pypi]
     username = __token__
     password = pypi-AgEI...твой-токен...

   [testpypi]
     username = __token__
     password = pypi-AgEI...токен-от-test.pypi.org...
   ```

   Логин — буквально `__token__`, пароль — сам токен (вместе с префиксом `pypi-`).
   Альтернатива без файла — переменные окружения на время заливки:

   ```bash
   export TWINE_USERNAME=__token__
   export TWINE_PASSWORD=pypi-AgEI...
   ```

---

## 2. Сборка дистрибутивов

Из корня репозитория:

```bash
rm -rf dist build *.egg-info
python3 -m build
```

Появятся два файла в `dist/`:

- `fidelis-0.1.0-py3-none-any.whl` — wheel (то, что ставится в большинстве случаев);
- `fidelis-0.1.0.tar.gz` — sdist (source distribution).

Версия берётся автоматически из `fidelis/__init__.py` (`__version__`) — отдельно
в `pyproject.toml` её дублировать не нужно.

---

## 3. Проверка артефактов

```bash
python3 -m twine check dist/*
```

Должно быть `PASSED` для обоих файлов. Дополнительно — установка из wheel в чистое
окружение и smoke-тест:

```bash
python3 -m venv /tmp/fidelis_check
/tmp/fidelis_check/bin/pip install "dist/fidelis-0.1.0-py3-none-any.whl[all]"
/tmp/fidelis_check/bin/python -c "import fidelis; print(fidelis.__version__)"
```

---

## 4. (Рекомендуется) Сначала TestPyPI

TestPyPI — отдельная песочница со своим аккаунтом и токеном
(<https://test.pypi.org>). Залей туда и проверь установку, прежде чем трогать
боевой PyPI:

```bash
python3 -m twine upload -r testpypi dist/*

# проверка установки из TestPyPI (основные зависимости тянем с обычного PyPI):
python3 -m pip install \
  --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ \
  fidelis
```

---

## 5. Публикация на боевой PyPI

```bash
python3 -m twine upload dist/*
```

После успеха пакет доступен:

```bash
pip install fidelis            # ядро
pip install "fidelis[all]"     # + Excel + HTTP-провайдеры
```

Страница проекта: <https://pypi.org/project/fidelis/>.

---

## 6. Выпуск новой версии

PyPI **не разрешает перезаливать** уже опубликованную версию. Для каждого релиза:

1. Подними версию в `fidelis/__init__.py`:

   ```python
   __version__ = "0.1.1"
   ```

   (семантическое версионирование: patch — фиксы, minor — новые фичи без слома,
   major — несовместимые изменения.)
2. Зафиксируй и поставь git-тег:

   ```bash
   git commit -am "Release 0.1.1"
   git tag v0.1.1 && git push --tags
   ```
3. Пересобери и залей:

   ```bash
   rm -rf dist build *.egg-info
   python3 -m build
   python3 -m twine check dist/*
   python3 -m twine upload dist/*
   ```

---

## 7. (Опционально) Автопубликация через GitHub Actions (Trusted Publishing)

Можно публиковать без токенов — через OIDC «trusted publisher». В репозитории уже
лежит готовый workflow [`.github/workflows/publish.yml`](.github/workflows/publish.yml),
который собирает и заливает пакет при создании GitHub Release.

Чтобы он заработал, один раз настрой доверенного издателя на PyPI:

1. <https://pypi.org/manage/account/publishing/> → **Add a new pending publisher**.
2. Заполни:
   - PyPI Project Name: `fidelis`
   - Owner: `NGdust`
   - Repository name: `fidelis`
   - Workflow name: `publish.yml`
   - Environment name: `pypi`
3. В GitHub создай environment `pypi` (Settings → Environments).

После этого каждый **GitHub Release** автоматически собирает и публикует пакет —
токены в репозитории не хранятся.

---

## Чеклист перед первым релизом

- [x] Имя `fidelis` свободно на PyPI
- [x] `python3 -m build` собирает wheel + sdist
- [x] `twine check dist/*` → PASSED
- [x] LICENSE (MIT) включён в дистрибутив
- [x] `py.typed` едет в wheel (PEP 561, типы экспортируются)
- [x] README рендерится как long description
- [ ] Создан API-токен PyPI
- [ ] Проверена установка с TestPyPI
- [ ] `twine upload dist/*` на боевой PyPI
