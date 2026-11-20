# Publishing guide

How to take this repository from a local folder to an installable, discoverable Home Assistant integration, and what is (and is not) realistic beyond that.

Repository slug used throughout: **`sobelman/ha-smartgrade`**, domain **`smartgrade`**. If you publish under another account, search-and-replace `sobelman/ha-smartgrade` (README.md, README.en.md, `manifest.json` `documentation` / `issue_tracker`) and `@sobelman` (`manifest.json` `codeowners`).

## 1. Repository layout

HACS and the Home Assistant Marketplace (built into HA 2026.11+, it uses the same catalog and `hacs.json`) expect **exactly one integration under `custom_components/`**:

```
ha-smartgrade/
├── custom_components/smartgrade/   <- what gets installed into <config>/custom_components/
│   ├── __init__.py, api.py, config_flow.py, coordinator.py, const.py,
│   ├── entity.py, switch.py, sensor.py, binary_sensor.py, diagnostics.py
│   ├── manifest.json               <- must contain "version"
│   ├── strings.json, translations/{en,he}.json
│   └── brand/icon.png, icon@2x.png <- local brand images (HA 2026.3+, satisfies the HACS "brands" check)
├── hacs.json                       <- name, country IL, minimum HA version
├── README.md / README.en.md        <- rendered by HACS (render_readme: true)
├── images/                         <- README images
├── tests/                          <- pytest + pytest-homeassistant-custom-component, mocked HTTP only
├── .github/workflows/validate.yaml <- HACS action + hassfest
├── .github/workflows/tests.yaml    <- ruff + pytest
├── LICENSE (MIT), PUBLISHING.md, findings.md, VERIFIED.md, docs/
```

Users who install manually copy only `custom_components/smartgrade`. HACS/Marketplace download the same folder from the release (or default branch if there are no releases).

## 2. Create the GitHub repository

```bash
cd ha-smartgrade
git init -b main
git add .
git commit -m "Initial release of the SmartGrade Secure integration"
gh repo create sobelman/ha-smartgrade --public --source . --push \
  --description "Unofficial Home Assistant integration for SmartGrade Secure (IoTech Verticals / Dealor) smart switches"
gh repo edit sobelman/ha-smartgrade --enable-issues \
  --add-topic home-assistant --add-topic hacs --add-topic homeassistant-integration \
  --add-topic hacs-integration --add-topic smartgrade --add-topic iotech --add-topic smart-home --add-topic israel
```

Before the first push, double-check nothing private slipped in:

```bash
git ls-files | grep -Ei '\.apk|jadx|mitm|pcap|token|session' ; echo "(should print nothing)"
git grep -nE 'eyJ[A-Za-z0-9_-]{10,}\.' ; echo "(no JWTs - should print nothing)"
```

The only credential-like value in the repository is the vendor's app-level `APP_PASSWORD` in `const.py` (see §6).

## 3. Make CI green

The push triggers:

- **Validate** (`validate.yaml`): `hacs/action` (category `integration`, **no ignores**) and `home-assistant/actions/hassfest`.
- **Tests** (`tests.yaml`): `ruff check`, `ruff format --check`, `pytest`.

Repository-level HACS checks that only pass once the GitHub repo is configured: **description**, **topics**, **issues enabled**, a **license GitHub detects** (MIT → OK), not archived. The commands in §2 cover them.

## 4. Release

HACS shows release tags as versions. Bump `version` in `manifest.json`, then:

```bash
# example for 0.2.0 — use the version you just put in manifest.json
git tag v0.2.0 && git push --tags
gh release create v0.2.0 --title "v0.2.0" --notes-file RELEASE_NOTES.md
```

Releases so far: `v0.1.0` (first public release), `v0.2.0` (read-only diagnostics, timers, next action, 3-phase metering, all product types).

`main` is protected (required status checks, linear history). Normally push changes through a pull request and merge once **Validate** and **Tests** are green.

A full GitHub **release** is required (a bare tag is not enough for the default catalog). Keep `manifest.json` `version` equal to the tag (without the `v`). Raise `hacs.json` `homeassistant` if you start using newer HA APIs.

At this point anyone can install it as a **custom repository**: HACS → Custom repositories → `https://github.com/sobelman/ha-smartgrade` (type Integration), or the My-link button in the README.

## 5. Getting into the HACS default catalog (optional)

The catalog is `hacs/default`, read by both HACS and the HA Marketplace. Requirements (see <https://www.hacs.xyz/docs/publish/include/> and <https://contributors.home-assistant.io/marketplace/guides/catalog/>):

1. You are the owner (or a major contributor) of the repository.
2. Public GitHub repo with description, topics, issues enabled, README, OSI license.
3. HACS action passing **without any `ignore`**, and hassfest passing.
4. At least one full GitHub release, created after the actions passed.
5. `hacs.json` has `name`; because this only works in Israel, `country: ["IL"]` is set (the catalog asks for it).
6. Brand images: `custom_components/smartgrade/brand/icon.png` is shipped (home-assistant/brands no longer accepts custom integrations).
7. The domain must not collide with a core integration (`smartgrade` does not, as of 2026.12.0.dev).

Then: fork `hacs/default`, branch from `master`, add `"sobelman/ha-smartgrade"` **alphabetically** to the `integration` file, open the PR **from your personal account** and fill in the template completely (links to the latest release, a passing HACS action run and a passing hassfest run). Expect a review backlog of **months**. Users can install via the custom-repository link meanwhile.

Catalog risk to be aware of: the HACS team removes repositories that "steal credentials" or are harmful. This integration only sends the user's phone number and token to the vendor's own API, which is fine, but say so clearly in the PR (the README Disclaimer already does).

## 6. Could this ever go into Home Assistant core? Honest answer: very unlikely

Core requirements (<https://contributors.home-assistant.io/core/integrations/create/#does-it-belong-in-core>) and what stands in the way:

| Core expectation | Status here |
|---|---|
| All device/API code in a separate **PyPI library** (dependency transparency) | Not done. `api.py` would have to move to e.g. `pysmartgrade` on PyPI, with its own CI and releases. Doable. |
| Product **established with a real user base** | Niche, Israel-only product. Questionable. |
| A **sanctioned API** / no shipping of extracted secrets | **Blocker.** The integration only works by using the app-level Basic credential `dealor_app:<password>` extracted from the vendor's APK. Core reviewers are very reluctant to ship credentials lifted from a vendor app, and the vendor could revoke it at any time, which would break every installation at once. |
| Stable, supportable API | Private, undocumented, may change with any app update. No vendor contact. |
| Good UX for auth | 30-day JWT with no refresh means a **monthly SMS reauth** for every user. Acceptable for a custom integration, poor for core. |
| Bronze quality scale, minimal first PR | Achievable (one platform, no diagnostics/reauth in the first PR). |

Realistic path: stay a **HACS/Marketplace custom integration**. Core only becomes plausible if IoTech Verticals publishes an official API (ideally OAuth2 or long-lived tokens) or provides partner credentials. If you want to try, contact the vendor first (`support@iotechv.com`).

## 7. Risks to state publicly (already in the README)

- **Hardcoded app credential.** `const.py` contains `APP_USER`/`APP_PASSWORD`, the vendor's app-level client credential, identical in every copy of the official app. It is not a user secret and grants nothing without a user's SMS-verified JWT, but: publishing it may displease the vendor; they can rotate it (integration breaks until updated); and GitHub secret scanning or HACS reviewers may ask about it. Keep the explanatory comment in `const.py`. Do not add any other credential.
- **30-day reauth.** HA raises a reauth notification when the JWT expires (checked locally from `exp` before every poll, and on any 401/403). The user must request and type a new SMS code. There is no way around this without a refresh endpoint.
- **Phone/HA session conflict.** Unverified whether logins invalidate each other. The integration never connects to MQTT with the user's id as clientId, which is known to kick the phone off the broker.
- **Unofficial API / ToS.** The vendor may object or change the API. Be ready to archive the repository if asked.
- **Safety.** These devices switch heaters. Never add tests or CI steps that talk to the real cloud or switch real devices.

## 8. Release checklist

- [ ] `ruff check . && ruff format --check . && pytest` pass locally
- [ ] `manifest.json` `version` bumped, equals the tag
- [ ] README(s) updated, screenshots added to `images/` (placeholders in README)
- [ ] No tokens / personal data: `git grep -nE 'eyJ[A-Za-z0-9_-]{10,}\.'` is empty, VERIFIED.md has no phone numbers or ids
- [ ] Validate + Tests workflows green on `main`
- [ ] `gh release create vX.Y.Z`
