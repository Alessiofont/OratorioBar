# Changelog

## 1.0.3
- La ricerca per nome tiene conto del formato (lattina, brick, bottiglia, vetro, busta) letto dal nome e dalla categoria:
  le foto di un formato diverso vengono scartate, quelle col formato confermato hanno la spunta verde e vanno in cima.
  La categoria (Bibite, Acqua, Snack salati…) aiuta a scartare prodotti di altro tipo.
- La foto automatica per nome viene assegnata solo se il formato corrisponde; altrimenti si sceglie a mano.
- "Cerca foto" non blocca più l'app: si può aprire un altro prodotto e cercare di nuovo; la ricerca precedente viene annullata
  e i suoi risultati non finiscono sul prodotto sbagliato. Pulsante "Annulla" mentre cerca.
- Nuovo pulsante "Elimina foto" nella scheda prodotto.
- Tempi di attesa più brevi verso le fonti delle foto.

## 1.0.2
- Foto: la ricerca per nome usa Search-a-licious di Open Food Facts, con la vecchia ricerca OFF e UPCitemdb come riserve.
- Foto per barcode: se Open Food Facts non ha il prodotto o non risponde, prova UPCitemdb.
- Una fonte che risponde 503/429 o va in timeout viene saltata per qualche minuto invece di bloccare ogni ricerca.
- Risultati delle ricerche in cache per 6 ore; "Cerca foto mancanti" non parte due volte in parallelo.

## 1.0.1
- Il certificato HTTPS viene preparato all'avvio, prima di mostrare il link: niente più errore SSL al primo accesso.
- Se la chiave Tailscale è scaduta o non valida, il registro mostra il link di accesso invece di fermarsi.

## 1.0.0
- Prima versione: carico a carrello in bozza (lotti = carrelli), scarichi FEFO, scadenze stimate,
  proposte da approvare, I miei movimenti (modifica entro 30 minuti), gestore lotti, Fare inventario,
  Correggi quantità, prospetto dinamico con scorta ideale dall'anno precedente, lista della spesa con archivio,
  log completo per l'amministratore, dispositivi collegati, notifiche ntfy, link dedicato con Tailscale Funnel.
