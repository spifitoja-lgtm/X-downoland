# X-downoland

GUI do ściągania zdjęć z X.com (Twitter) — zarówno bez logowania (limit ~30 mediów), jak i po zalogowaniu (pełna historia profilu).

## Windows .exe

Najnowszy build w zakładce **Releases** — pobierz `X-downoland-windows.zip`, rozpakuj, odpal `X-downoland.exe`.

## macOS / Linux (z kodu)

```bash
uv run x_scraper.py
```

## Jak działa

- **Bez logowania**: publiczny endpoint `syndication.twitter.com` (ten od embedów). Działa od ręki, ale X zwraca tylko ostatnie ~30 mediów.
- **Z logowaniem**: GraphQL API + cookies, pełna historia. Klik **Zaloguj (import z przeglądarki)** → wybierz przeglądarkę, w której jesteś zalogowany na x.com → cookies są zaczytane automatycznie (przez `browser-cookie3`). Pomiędzy startami zapamiętane w `~/.config/x-downoland/` (mac/Linux) lub `%APPDATA%\X-downoland\` (Windows).

Zdjęcia pobierane są w oryginalnej rozdzielczości (`?name=orig`).
