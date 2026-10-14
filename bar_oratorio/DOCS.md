# Bar Oratorio

Inventario del bar dell'oratorio. L'app gira dentro Home Assistant ma si apre con un **link suo**
(`https://bar-oratorio.<tua-tailnet>.ts.net`), separato da Home Assistant e da Nabu Casa.
Dati e foto stanno in `/data` dell'add-on e finiscono nei backup di Home Assistant.

## 1. Prima di installare: Tailscale

Il link dedicato è creato da un piccolo Tailscale interno all'add-on (non tocca l'add-on Tailscale
già presente). Nella console di Tailscale (https://login.tailscale.com/admin), sulla tailnet che vuoi usare:

1. **DNS** → attiva **MagicDNS** e **HTTPS Certificates**.
2. **Access controls** → controlla che ci sia la regola per Funnel (nelle tailnet nuove c'è già):
   ```json
   "nodeAttrs": [
     { "target": ["autogroup:member"], "attr": ["funnel"] }
   ]
   ```
3. **Settings → Keys → Generate auth key**: lascia *Reusable* spento, attiva *Pre-approved* se
   l'approvazione dei dispositivi è attiva. Copia la chiave (`tskey-auth-…`).

## 2. Configurazione dell'add-on

| Opzione | Cosa mettere |
|---|---|
| Nome utente amministratore | il nome con cui entra l'oratorio (predefinito `oratorio`) |
| Password amministratore | almeno 8 caratteri, serve al primo avvio |
| Reimposta password | lascialo spento; accendilo solo per recuperare la password, poi rispegnilo |
| Usa Tailscale | acceso |
| Chiave di accesso Tailscale | la chiave del punto 1.3 (serve solo la prima volta) |
| Nome del dispositivo Tailscale | `bar-oratorio` → diventa l'inizio del link |
| Link pubblico (Funnel) | acceso per aprirlo da qualsiasi telefono |

Avvia l'add-on e apri la scheda **Registro**: dopo qualche secondo compare

```
Bar Oratorio raggiungibile su: https://bar-oratorio.<tailnet>.ts.net
```

Poi, nella console Tailscale → **Machines** → `bar-oratorio` → **Disable key expiry**, così il
collegamento non scade dopo sei mesi.

## 3. Primo accesso

1. Apri il link, entra con il nome utente e la password dell'amministratore.
2. **Utenti e dispositivi** → crea coordinatori e operatori, ognuno con il suo PIN.
3. **Impostazioni** → controlla le durate tipiche delle categorie (per le scadenze stimate)
   e iscrivi i telefoni ai canali **ntfy** (app gratuita ntfy, «+», nome del canale o QR).
4. **Prodotti** → crea l'anagrafica (anche da CSV), poi fai il primo **Fare inventario**
   per caricare le giacenze iniziali.

Sui telefoni conviene usare «Aggiungi a schermata Home» dal browser: si apre come un'app.

## Accesso dalla rete locale (facoltativo)

Nella scheda **Rete** puoi assegnare una porta all'8080 (es. 8080): l'app si apre anche su
`http://<ip-di-home-assistant>:8080`. Senza https la fotocamera per i barcode non funziona:
usa il link Tailscale sui telefoni.

## Problemi comuni

- **Il link non si apre / errore TLS**: nel registro cerca le righe `[tailscale funnel]`. Se c'è un link
  per abilitare Funnel, aprilo e riavvia l'add-on. Se il problema compare dopo giorni di funzionamento,
  riavviare l'add-on azzera e ricrea la configurazione di Funnel.
- **Nel registro c'è un link `login.tailscale.com/a/…`**: manca o è scaduta la chiave; aprilo e accedi,
  oppure metti una nuova chiave nelle opzioni.
- **Password dell'amministratore dimenticata**: scrivi la nuova password nelle opzioni, accendi
  «Reimposta password», riavvia, poi rispegni l'opzione.
- **Fotocamera che non parte**: deve essere il link `https`; consenti l'accesso alla fotocamera quando il
  browser lo chiede. In alternativa si può scrivere il codice o usare un lettore barcode USB/Bluetooth.
