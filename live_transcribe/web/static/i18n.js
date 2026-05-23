/* Volksmond interface translations.
 *
 * Keyed by the exact English UI string. app.js translates any text set via the
 * `el` helper through this map when the interface language is Afrikaans, so this
 * file is the ONLY place a translator needs to touch. Dynamic content (transcript
 * text, device names, file names, summaries) is never a key here, so it always
 * passes through untranslated.
 *
 * House style: South African Afrikaans, natural and plain. No em or en dashes.
 * Leave a value out (or delete it) to fall back to the English string.
 */
window.VM_I18N = {
  af: {
    // Language names (shown in the picker)
    "English (South Africa)": "Engels (Suid-Afrika)",
    "Afrikaans": "Afrikaans",
    "English": "Engels",

    // Sidebar / navigation
    "Meeting": "Vergadering",
    "History": "Geskiedenis",
    "Settings": "Instellings",
    "Local only, no internet": "Net plaaslik, geen internet",
    "Report a bug or idea": "Meld 'n fout of idee",

    // First-run: welcome
    "working name": "werksnaam",
    "A calm, private transcript of any meeting on your computer.":
      "'n Rustige, private transkripsie van enige vergadering op jou rekenaar.",
    "Volksmond listens to your microphone and the audio coming out of your computer, and writes it down as people talk. Built for Afrikaans, English, and the way people actually switch between them.":
      "Volksmond luister na jou mikrofoon en die klank wat uit jou rekenaar kom, en skryf dit neer terwyl mense praat. Gebou vir Afrikaans, Engels, en die manier waarop mense werklik tussen die twee wissel.",
    "Your audio never leaves this computer.": "Jou klank verlaat nooit hierdie rekenaar nie.",
    "No cloud, no third-party servers, no telemetry. Everything is transcribed locally, on your machine. You can use Volksmond completely offline.":
      "Geen wolk, geen derdeparty-bedieners, geen telemetrie nie. Alles word plaaslik getranskribeer, op jou masjien. Jy kan Volksmond heeltemal vanlyn gebruik.",
    "Get started": "Kom ons begin",
    "The language model for transcription is installed with the app. Summaries are an optional extra you can turn on next.":
      "Die taalmodel vir transkripsie word saam met die program geïnstalleer. Opsommings is 'n opsionele ekstra wat jy volgende kan aanskakel.",

    // First-run: summaries question
    "Setup, summaries": "Opstelling, opsommings",
    "Do you want to just transcribe, or also summarise on your machine?":
      "Wil jy net transkribeer, of ook op jou masjien opsom?",
    "Summaries condense a finished transcript into the decisions, the to-dos, and what stayed unresolved. They run on a small model on your machine, separate from the one that does the transcribing. Off by default.":
      "Opsommings vat 'n voltooide transkripsie saam in die besluite, die take, en wat onopgelos gebly het. Hulle loop op 'n klein model op jou masjien, apart van die een wat transkribeer. Standaard af.",
    "Just transcribe": "Net transkribeer",
    "The original promise. Live transcripts, history, all of it. No extra model, no extra RAM.":
      "Die oorspronklike belofte. Lewendige transkripsies, geskiedenis, alles. Geen ekstra model, geen ekstra RAM nie.",
    "Default. You can turn summaries on later in Settings.":
      "Standaard. Jy kan opsommings later in Instellings aanskakel.",
    "Transcribe and summarise": "Transkribeer en som op",
    "Adds a Summarise button at the end of every meeting, run entirely on this machine.":
      "Voeg 'n Opsom-knoppie aan die einde van elke vergadering by, heeltemal op hierdie masjien.",
    "A summary model is already installed on this machine.":
      "'n Opsommingsmodel is reeds op hierdie masjien geïnstalleer.",
    "Needs a summary model file in your models folder. You can set this up in Settings.":
      "Benodig 'n opsommingsmodel-lêer in jou models-vouer. Jy kan dit in Instellings opstel.",
    "Skip for now": "Slaan vir nou oor",
    "Continue": "Gaan voort",
    "Default": "Standaard",

    // Home / new-session hub
    "Ready when you are": "Reg wanneer jy is",
    "Start a session": "Begin 'n sessie",
    "Three ways in. Pick the one that fits the moment.":
      "Drie maniere in. Kies die een wat by die oomblik pas.",
    "Most common": "Mees algemeen",
    "Start a live meeting": "Begin 'n lewendige vergadering",
    "Begin": "Begin",
    "Transcribe what you and others are saying right now, on this computer. Optionally record the audio too.":
      "Transkribeer wat jy en ander nou sê, op hierdie rekenaar. Neem opsioneel ook die klank op.",
    "Upload a recording to transcribe": "Laai 'n opname op om te transkribeer",
    "Choose a file": "Kies 'n lêer",
    "Pick an audio or video file you already have. Volksmond transcribes it locally, just like a live meeting.":
      "Kies 'n klank- of videolêer wat jy reeds het. Volksmond transkribeer dit plaaslik, net soos 'n lewendige vergadering.",
    "Record only, transcribe later": "Neem net op, transkribeer later",
    "Start recording": "Begin opneem",
    "For machines that cannot keep up live. Volksmond records the audio cleanly, and you transcribe it when you are back at a desk.":
      "Vir masjiene wat nie lewendig kan byhou nie. Volksmond neem die klank skoon op, en jy transkribeer dit wanneer jy terug by 'n lessenaar is.",
    "Have a recording already?": "Het jy reeds 'n opname?",
    "Up to several hours. The file stays on this computer. It is never uploaded.":
      "Tot etlike ure. Die lêer bly op hierdie rekenaar. Dit word nooit opgelaai nie.",
    "Browse": "Blaai",

    // Pre-meeting
    "Start a meeting": "Begin 'n vergadering",
    "Press begin when the meeting starts. You can add names and jargon before you begin.":
      "Druk begin wanneer die vergadering begin. Jy kan name en vakterme byvoeg voordat jy begin.",
    "On this machine": "Op hierdie masjien",
    "Meeting title": "Vergaderingtitel",
    " (optional)": " (opsioneel)",
    "Language": "Taal",
    "Quality": "Gehalte",
    "Auto-detect": "Outomaties",
    "Auto": "Outo",
    "Fast": "Vinnig",
    "Balanced": "Gebalanseerd",
    "Best": "Beste",
    "Names and jargon": "Name en vakterme",
    " (optional, helps accuracy)": " (opsioneel, help akkuraatheid)",
    "Add a term": "Voeg 'n term by",
    "Your saved default context is applied automatically. Add anything specific to this meeting here.":
      "Jou gestoorde verstekkonteks word outomaties toegepas. Voeg enigiets spesifiek tot hierdie vergadering hier by.",
    "Record the audio": "Neem die klank op",
    "Keeps the audio on this machine until you stop. Lets you transcribe or summarise it again later, more accurately.":
      "Hou die klank op hierdie masjien tot jy stop. Laat jou dit later weer transkribeer of opsom, meer akkuraat.",
    "Courtesy line you could say": "Beleefdheidsin wat jy kan sê",
    "Just a heads-up, I am running a tool on my machine that is taking a private transcript for my own notes. The audio does not leave my computer.":
      "Net so jy weet, ek gebruik 'n program op my masjien wat 'n private transkripsie vir my eie notas neem. Die klank verlaat nie my rekenaar nie.",
    "Audio sources": "Klankbronne",
    "Your microphone": "Jou mikrofoon",
    "System audio (everyone else)": "Stelselklank (almal anders)",
    "Your voice comes from the microphone. Everyone else comes from your computer's own audio.":
      "Jou stem kom van die mikrofoon. Almal anders kom van jou rekenaar se eie klank.",
    "not detected": "nie bespeur nie",
    "Stays on this computer": "Bly op hierdie rekenaar",
    "No audio, transcript, or metadata is sent anywhere. Offline-safe.":
      "Geen klank, transkripsie of metadata word êrens gestuur nie. Vanlyn-veilig.",
    "Back": "Terug",
    "Audio stays on this machine unless you opt in.":
      "Klank bly op hierdie masjien tensy jy inteken.",

    // Live screen
    "Finishing": "Maak klaar",
    "Listening": "Luister",
    "Saved": "Gestoor",
    "Model on this device": "Model op die toestel",
    "Local only": "Net plaaslik",
    "Live meeting": "Lewendige vergadering",
    "Stop and save": "Stop en stoor",
    "Stop": "Stop",
    "Recording audio": "Neem klank op",
    "Saving to ": "Stoor na ",
    "Listening. The transcript appears here as people talk.":
      "Luister. Die transkripsie verskyn hier soos mense praat.",

    // Stop menu
    "You have recording and transcription on": "Jy het opname en transkripsie aan",
    "Stop transcription, keep recording": "Stop transkripsie, hou opname aan",
    "Falls back to a quiet recording. Transcribe and summarise it after the meeting.":
      "Val terug na 'n stil opname. Transkribeer en som dit op ná die vergadering.",
    "Stop recording, keep transcribing": "Stop opname, hou transkripsie aan",
    "The live transcript continues. Nothing more is saved as audio.":
      "Die lewendige transkripsie gaan voort. Niks meer word as klank gestoor nie.",
    "Stop recording and transcription": "Stop opname en transkripsie",
    "End the session and save what you have.": "Beëindig die sessie en stoor wat jy het.",
    "Recommended": "Aanbeveel",

    // Record-only
    "Recording": "Opname",
    "Recording only, not transcribing yet": "Neem net op, transkribeer nog nie",
    "Recording cleanly. No transcript is being made right now. When you stop, you can transcribe it here.":
      "Neem skoon op. Geen transkripsie word nou gemaak nie. Wanneer jy stop, kan jy dit hier transkribeer.",
    "Stop recording": "Stop opname",
    "Saving": "Stoor",
    "Recording saved.": "Opname gestoor.",
    "Transcribe this recording now?": "Transkribeer hierdie opname nou?",
    "Volksmond will read the file and write it out. Slower than live, but more accurate. You can keep working while it runs. Stays on this computer.":
      "Volksmond sal die lêer lees en dit uitskryf. Stadiger as lewendig, maar meer akkuraat. Jy kan aanhou werk terwyl dit loop. Bly op hierdie rekenaar.",
    "Transcribe this recording now": "Transkribeer hierdie opname nou",
    "Transcribe later": "Transkribeer later",
    "You can transcribe a recording any time from ": "Jy kan 'n opname enige tyd transkribeer vanaf ",
    ". Recordings are kept until you delete them.": ". Opnames word gehou tot jy hulle uitvee.",

    // Importing
    "Cancel": "Kanselleer",
    "Stopping.": "Stop tans.",
    "Reading the file. You can leave this open or come back later.":
      "Lees die lêer. Jy kan dit oop laat of later terugkom.",
    "Will save to ": "Sal stoor na ",
    "Reading the file. The transcript appears here as it goes.":
      "Lees die lêer. Die transkripsie verskyn hier soos dit vorder.",

    // Finish + summarise
    "Saved.": "Gestoor.",
    "Saved, with a warning": "Gestoor, met 'n waarskuwing",
    "Saving may not have completed": "Stoor het dalk nie voltooi nie",
    "Open folder": "Maak vouer oop",
    "Open transcript": "Maak transkripsie oop",
    "Copy": "Kopieer",
    "Done": "Klaar",
    "Summarise this transcript": "Som hierdie transkripsie op",
    "No summary model is set up on this computer yet. Choose one in Settings, then summaries run here, fully on-device.":
      "Geen opsommingsmodel is nog op hierdie rekenaar opgestel nie. Kies een in Instellings, dan loop opsommings hier, heeltemal op die toestel.",
    "Set up summaries": "Stel opsommings op",
    "Working on your summary": "Werk aan jou opsomming",
    "Reading the full transcript on this machine. This takes a little while.":
      "Lees die volle transkripsie op hierdie masjien. Dit neem 'n rukkie.",
    "Runs on this computer using your installed model. Produces decisions, action items, and open questions.":
      "Loop op hierdie rekenaar met jou geïnstalleerde model. Lewer besluite, aksie-items en oop vrae.",
    "Summarise": "Som op",
    "Summary": "Opsomming",
    "Ran on this computer, saved next to the transcript": "Het op hierdie rekenaar geloop, gestoor langs die transkripsie",
    "Regenerate": "Genereer weer",
    "Saved as ": "Gestoor as ",
    ", next to the transcript. Nothing was sent off this computer.":
      ", langs die transkripsie. Niks is van hierdie rekenaar af gestuur nie.",

    // History
    "Past meetings": "Vorige vergaderings",
    "Search transcripts": "Soek transkripsies",
    "New meeting": "Nuwe vergadering",
    "No meetings yet.": "Nog geen vergaderings nie.",
    "Once you transcribe a meeting, it shows up here. Nothing is uploaded; your meetings live in your data folder.":
      "Sodra jy 'n vergadering transkribeer, verskyn dit hier. Niks word opgelaai nie; jou vergaderings leef in jou data-vouer.",
    "Open": "Maak oop",
    "Saved in ": "Gestoor in ",

    // Reader
    "Back to history": "Terug na geskiedenis",
    "Folder": "Vouer",

    // Settings
    "Pro, activated": "Pro, geaktiveer",
    "Free": "Gratis",
    "Perpetual": "Ewigdurend",
    "Calendar attendee seeding and the optional online fallbacks are unlocked. Verified on this computer, never on a server.":
      "Kalender-bywonername en die opsionele aanlyn-terugvalle is ontsluit. Geverifieer op hierdie rekenaar, nooit op 'n bediener nie.",
    "Unlimited local transcription and summaries, forever. Pro adds calendar attendees and optional online fallbacks for weak machines.":
      "Onbeperkte plaaslike transkripsie en opsommings, vir altyd. Pro voeg kalender-bywoners en opsionele aanlyn-terugvalle vir swak masjiene by.",
    "Deactivate": "Deaktiveer",
    "Upgrade": "Opgradeer",
    "Appearance": "Voorkoms",
    "Theme": "Tema",
    "System follows your operating system. Dark uses the same palette, inverted.":
      "Stelsel volg jou bedryfstelsel. Donker gebruik dieselfde palet, omgekeer.",
    "System": "Stelsel",
    "Light": "Lig",
    "Dark": "Donker",
    "Interface language": "Koppelvlaktaal",
    "The language Volksmond shows you. It does not change what gets transcribed.":
      "Die taal waarin Volksmond vir jou wys. Dit verander nie wat getranskribeer word nie.",
    "Transcription": "Transkripsie",
    "Default language": "Verstektaal",
    "Used unless you change it for a meeting.": "Gebruik tensy jy dit vir 'n vergadering verander.",
    "Auto picks the best model your hardware can run.":
      "Outo kies die beste model wat jou hardeware kan loop.",
    "Best (GPU)": "Beste (GPU)",
    "Default context, names and jargon": "Verstekkonteks, name en vakterme",
    "Applied to every meeting to help accuracy. Stored on this computer only.":
      "Toegepas op elke vergadering om akkuraatheid te help. Net op hierdie rekenaar gestoor.",
    "Save context": "Stoor konteks",
    "Summaries, run on this machine": "Opsommings, loop op hierdie masjien",
    "Summary model": "Opsommingsmodel",
    "Change": "Verander",
    "Choose model": "Kies model",
    "Installed": "Geïnstalleer",
    "Open data folder": "Maak data-vouer oop",
    "See the transcripts and any models stored on this computer.":
      "Sien die transkripsies en enige modelle wat op hierdie rekenaar gestoor is.",
    "Data and privacy": "Data en privaatheid",
    "Save transcripts and recordings to": "Stoor transkripsies en opnames na",
    "Saved to a cloud folder": "Gestoor na 'n wolkvouer",
    "Your transcripts sync to this cloud service, not only this computer. They are never sent anywhere for processing. Pick a local folder to keep them on this machine only.":
      "Jou transkripsies sinkroniseer na hierdie wolkdiens, nie net hierdie rekenaar nie. Hulle word nooit êrens heen gestuur vir verwerking nie. Kies 'n plaaslike vouer om hulle net op hierdie masjien te hou.",
    "Audio is off by default": "Klank is standaard af",
    "Recording is only kept when you switch it on for a meeting. The privacy promise holds otherwise.":
      "Opname word net gehou wanneer jy dit vir 'n vergadering aanskakel. Andersins geld die privaatheidsbelofte.",
    "On by you only": "Net deur jou aan",
    "Danger zone, these settings can send data off your computer":
      "Gevaarsone, hierdie instellings kan data van jou rekenaar af stuur",
    "Online API key for a future fallback": "Aanlyn API-sleutel vir 'n toekomstige terugval",
    "For weak machines that cannot keep up. When an online fallback is enabled in a later version, audio or transcript text would be sent to the provider you choose. Your data would leave this computer. Not recommended for counselling, legal, or any confidential context. The key is stored encrypted on this machine.":
      "Vir swak masjiene wat nie kan byhou nie. Wanneer 'n aanlyn-terugval in 'n latere weergawe geaktiveer word, sou klank of transkripsieteks na die verskaffer van jou keuse gestuur word. Jou data sou hierdie rekenaar verlaat. Nie aanbeveel vir berading, regsake, of enige vertroulike konteks nie. Die sleutel word geënkripteer op hierdie masjien gestoor.",
    "Clear key": "Vee sleutel uit",
    "Save key": "Stoor sleutel",

    // Upgrade
    "Pro adds the polish, not the privacy.": "Pro voeg die afronding by, nie die privaatheid nie.",
    "Free is the real thing: unlimited live transcription and local summaries, on this machine, forever. Pro adds the few features that actually need to reach the internet.":
      "Gratis is die regte ding: onbeperkte lewendige transkripsie en plaaslike opsommings, op hierdie masjien, vir altyd. Pro voeg die paar funksies by wat werklik die internet nodig het.",
    "Unlimited local live transcription": "Onbeperkte plaaslike lewendige transkripsie",
    "Local summaries, on this machine": "Plaaslike opsommings, op hierdie masjien",
    "Afrikaans, English, and the mix": "Afrikaans, Engels, en die mengsel",
    "Save and export, fully offline": "Stoor en voer uit, heeltemal vanlyn",
    "Pull attendee names from your calendar": "Trek bywonername uit jou kalender",
    "Optional online transcription for weak machines": "Opsionele aanlyn-transkripsie vir swak masjiene",
    "Optional online summary for harder transcripts": "Opsionele aanlyn-opsomming vir moeiliker transkripsies",
    "Pro covers only what needs an online connection. Everything that runs on this computer stays free. Perpetual: you own this version forever.":
      "Pro dek net wat 'n aanlyn-verbinding nodig het. Alles wat op hierdie rekenaar loop, bly gratis. Ewigdurend: jy besit hierdie weergawe vir altyd.",
    "Already bought": "Reeds gekoop",
    "Opens the Volksmond website in your browser. You get a licence key by email after purchase, and activation is fully offline.":
      "Maak die Volksmond-webwerf in jou blaaier oop. Jy kry 'n lisensiesleutel per e-pos ná aankoop, en aktivering is heeltemal vanlyn.",
    "Activate": "Aktiveer",
    "Your key is checked on this computer, never on a server. No account, no phone-home.":
      "Jou sleutel word op hierdie rekenaar nagegaan, nooit op 'n bediener nie. Geen rekening, geen tuisbel nie.",

    // Modal (paste path fallback)
    "Type a folder path": "Tik 'n vouer-pad",
    "Type a file path": "Tik 'n lêer-pad",
    "No native file dialog is available here. Paste the full path on this computer.":
      "Geen inheemse lêer-dialoog is hier beskikbaar nie. Plak die volle pad op hierdie rekenaar.",
    "Use this path": "Gebruik hierdie pad",

    // About / credit
    "by DigiPhyte": "deur DigiPhyte",
    "About": "Oor",
    "Said FOLKS-mont. Afrikaans for the way people actually speak.":
      "Uitgespreek FOLKS-mont. Afrikaans vir hoe mense werklik praat.",

    // Report a bug
    "Nothing is sent automatically. The app never phones home, you send this yourself.":
      "Niks word outomaties gestuur nie. Die program bel nooit huis toe nie, jy stuur dit self.",
    "Send it to": "Stuur dit na",
    "Copy report": "Kopieer verslag",
    "Open email": "Maak e-pos oop",
    "Close": "Maak toe",
    "A DigiPhyte product, built in South Africa. All transcription happens on this machine unless you explicitly opt in.":
      "'n DigiPhyte-produk, gebou in Suid-Afrika. Alle transkripsie gebeur op hierdie masjien tensy jy uitdruklik inteken.",
  },
};
