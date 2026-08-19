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
    "Report a bug or idea": "Meld 'n gogga of idee",

    // First-run: licence agreement (the un-skippable gate)
    "Free for personal use. Business use needs a licence.":
      "Gratis vir persoonlike gebruik. Sakegebruik benodig 'n lisensie.",
    "Use Volksmond for your own meetings, study, or personal projects and it is free, forever. If a business or practice uses it for work, that needs a paid licence. One or two people trying it at work is fine; rolling it out to a team or using it in paid client work is what a licence is for.":
      "Gebruik Volksmond vir jou eie vergaderings, studie of persoonlike projekte en dit is gratis, vir altyd. As 'n besigheid of praktyk dit vir werk gebruik, is 'n betaalde lisensie nodig. Een of twee mense wat dit by die werk probeer is reg; om dit na 'n span uit te rol of in betaalde kliëntewerk te gebruik is waarvoor 'n lisensie bedoel is.",
    "Personal use: free, everything on this computer, no account.":
      "Persoonlike gebruik: gratis, alles op hierdie rekenaar, geen rekening nie.",
    "Business use: a paid licence per person, renewed yearly.":
      "Sakegebruik: 'n betaalde lisensie per persoon, jaarliks hernu.",
    "Your audio never leaves this computer either way.":
      "Jou klank verlaat in elk geval nooit hierdie rekenaar nie.",
    "It runs on the honour system.": "Dit werk op die eresisteem.",
    "Volksmond never phones home. There is no account, no activation server, and no way for us to see that you installed it or how you use it. We are trusting you: if a business or practice uses Volksmond for work, buy a licence. That trust is what keeps the personal version free and the Afrikaans models open for everyone.":
      "Volksmond bel nooit huis toe nie. Daar is geen rekening, geen aktiveringsbediener, en geen manier vir ons om te sien dat jy dit geïnstalleer het of hoe jy dit gebruik nie. Ons vertrou jou: as 'n besigheid of praktyk Volksmond vir werk gebruik, koop 'n lisensie. Daardie vertroue is wat die persoonlike weergawe gratis hou en die Afrikaanse modelle oop hou vir almal.",
    "I agree and continue": "Ek stem saam en gaan voort",
    "Read the full licence": "Lees die volledige lisensie",

    // First-run: welcome
    "Research Preview": "Navorsingsvoorskou",
    "A calm, private transcript of any meeting on your computer.":
      "'n Rustige, private transkripsie van enige vergadering op jou rekenaar.",
    "Volksmond listens to your microphone and the audio coming out of your computer, and writes it down as people talk. Built for Afrikaans, English, and the way people actually switch between them.":
      "Volksmond luister na jou mikrofoon en die klank wat uit jou rekenaar kom, en skryf dit neer terwyl mense praat. Gebou vir Afrikaans, Engels, en die manier waarop mense werklik tussen die twee wissel.",
    "Your audio never leaves this computer.": "Jou klank verlaat nooit hierdie rekenaar nie.",
    "No telemetry and no accounts. Your audio and transcripts are never uploaded; everything is transcribed and summarised on your own machine. Once the models are downloaded, you can use Volksmond completely offline.":
      "Geen telemetrie en geen rekeninge nie. Jou klank en transkripsies word nooit opgelaai nie; alles word op jou eie masjien getranskribeer en opgesom. Sodra die modelle afgelaai is, kan jy Volksmond heeltemal vanlyn gebruik.",
    "Get started": "Kom ons begin",
    "Next we download the transcription model to your computer, so your first meeting starts straight away. Summaries are an optional extra you can add after that.":
      "Vervolgens laai ons die transkripsiemodel na jou rekenaar af, sodat jou eerste vergadering dadelik begin. Opsommings is 'n opsionele ekstra wat jy daarna kan byvoeg.",

    // First-run: languages
    "Setup, languages": "Opstelling, tale",
    "Which languages do you transcribe?": "Watter tale transkribeer jy?",
    "Pick the languages you record in. Afrikaans uses Fluister, our Afrikaans-tuned model; English and the rest use standard Whisper. The size is chosen automatically for your computer.":
      "Kies die tale waarin jy opneem. Afrikaans gebruik Fluister, ons Afrikaans-gestemde model; Engels en die res gebruik standaard Whisper. Die grootte word outomaties vir jou rekenaar gekies.",
    "You can change this any time in Settings.": "Jy kan dit enige tyd in Instellings verander.",

    // First-run: transcription model
    "Setup, transcription model": "Opstelling, transkripsiemodel",
    "Afrikaans uses Fluister, downloaded automatically the first time you transcribe Afrikaans. The model below is the standard Whisper model for English and other languages.":
      "Afrikaans gebruik Fluister, outomaties afgelaai die eerste keer wat jy Afrikaans transkribeer. Die model hieronder is die standaard Whisper-model vir Engels en ander tale.",
    "Download the model that does the transcribing": "Laai die model af wat die transkripsie doen",
    "Volksmond transcribes on your own computer using a language model. Download the one that suits your machine now, so your first meeting starts straight away instead of waiting on a download. It runs offline afterwards.":
      "Volksmond transkribeer op jou eie rekenaar met 'n taalmodel. Laai nou die een af wat by jou masjien pas, sodat jou eerste vergadering dadelik begin in plaas daarvan om vir 'n aflaai te wag. Dit loop daarna vanlyn.",
    "It downloads in the background. You can carry on with setup while it finishes; your first meeting waits for it to be ready.":
      "Dit laai in die agtergrond af. Jy kan met die opstelling aangaan terwyl dit klaarmaak; jou eerste vergadering wag totdat dit gereed is.",
    "Download recommended and continue": "Laai aanbevole af en gaan voort",

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
      "Benodig 'n opsommingsmodel-lêer in jou models-gids. Jy kan dit in Instellings opstel.",
    "Skip for now": "Slaan vir nou oor",
    "Continue": "Gaan voort",
    "Default": "Standaard",

    // Home / new-session hub
    "Ready when you are": "Reg wanneer jy is",
    "Start a session": "Begin 'n sessie",
    "Three ways in. Pick the one that fits the moment.":
      "Drie maniere in. Kies die een wat by die oomblik pas.",
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
    "Record only": "Slegs opname",
    "Name this recording": "Benoem hierdie opname",
    "Volksmond records the audio cleanly on this computer. No transcript is made while recording. You can transcribe it later.":
      "Volksmond neem die klank skoon op hierdie rekenaar op. Geen transkripsie word tydens opname gemaak nie. Jy kan dit later transkribeer.",
    "Recording name": "Opname se naam",
    "For machines that cannot keep up live. Volksmond records the audio cleanly, and you transcribe it when you are back at a desk.":
      "Vir masjiene wat nie lewendig kan byhou nie. Volksmond neem die klank skoon op, en jy transkribeer dit wanneer jy terug by 'n lessenaar is.",
    "Have a recording already?": "Het jy reeds 'n opname?",
    // Import setup (context before transcribing a file)
    "Before we transcribe": "Voor ons transkribeer",
    "Add context": "Voeg konteks by",
    "Names and jargon help accuracy, especially for Afrikaans and the mix. All optional.":
      "Name en vakterme help akkuraatheid, veral vir Afrikaans en die mengsel. Alles opsioneel.",
    "Title": "Titel",
    "Transcribe": "Transkribeer",
    "Participants": "Deelnemers",
    "Jargon and terms": "Vakterme en jargon",
    "Add a name": "Voeg 'n naam by",
    "Always applied (from Settings)": "Altyd toegepas (van Instellings)",
    "Tip: save company names and jargon in Settings and they apply to every transcription automatically.":
      "Wenk: stoor maatskappyname en vakterme in Instellings en hulle word outomaties op elke transkripsie toegepas.",
    "Context for this meeting": "Konteks vir hierdie vergadering",
    " edit for this meeting": " wysig vir hierdie vergadering",
    "Starts from your saved default. Edits here apply to this meeting only; your saved default in Settings is unchanged.":
      "Begin by jou gestoorde verstek. Wysigings hier geld net vir hierdie vergadering; jou gestoorde verstek in Instellings bly onveranderd.",
    "Applies to this meeting only. To reuse it every time, save it in Settings.":
      "Geld net vir hierdie vergadering. Om dit elke keer te gebruik, stoor dit in Instellings.",
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
    "Engine: ": "Enjin: ",
    "Afrikaans-optimised model": "Afrikaans-geoptimeerde model",
    "Afrikaans uses Fluister, our Afrikaans-tuned model. The size is chosen automatically for your computer.":
      "Afrikaans gebruik Fluister, ons Afrikaans-gestemde model. Die grootte word outomaties vir jou rekenaar gekies.",
    "Afrikaans currently uses standard Whisper. The Afrikaans-tuned Fluister model is not installed yet; it switches on automatically once it is.":
      "Afrikaans gebruik tans standaard Whisper. Die Afrikaans-gestemde Fluister-model is nog nie geïnstalleer nie; dit skakel outomaties aan sodra dit wel is.",
    "Auto-detect uses standard Whisper. The size is chosen automatically for your computer.":
      "Outomaties gebruik standaard Whisper. Die grootte word outomaties vir jou rekenaar gekies.",
    "English uses standard Whisper. The size is chosen automatically for your computer.":
      "Engels gebruik standaard Whisper. Die grootte word outomaties vir jou rekenaar gekies.",
    "Auto-detect uses Fluister, our Afrikaans-tuned model. The size is chosen automatically for your computer.":
      "Outo-bespeur gebruik Fluister, ons Afrikaans-gestemde model. Die grootte word outomaties vir jou rekenaar gekies.",
    "Fluister (our Afrikaans-tuned model) is not installed on this computer yet, so this runs on standard Whisper for now.":
      "Fluister (ons Afrikaans-gestemde model) is nog nie op hierdie rekenaar geïnstalleer nie, so dit loop voorlopig op standaard Whisper.",
    "Forced to Fluister for every language. Handy when an English meeting has Afrikaans words mixed in.":
      "Gedwing na Fluister vir elke taal. Handig wanneer 'n Engelse vergadering Afrikaanse woorde inmeng.",
    "Forced to standard Whisper for every language.":
      "Gedwing na standaard Whisper vir elke taal.",
    "Engine": "Enjin",
    " (auto follows the language)": " (outo volg die taal)",
    "Auto picks the model for your language: Fluister for Afrikaans and auto-detect, Swivuriso for South African languages, Whisper for the rest. Force one to override.":
      "Outo kies die model vir jou taal: Fluister vir Afrikaans en outo-bespeur, Swivuriso vir Suid-Afrikaanse tale, en Whisper vir die res. Kies een om dit te oorskryf.",
    "Advanced": "Gevorderd",
    "Model size": "Modelgrootte",
    " (auto is recommended)": " (outo word aanbeveel)",
    "Auto picks the best model your computer can run. Bigger is more accurate but slower.":
      "Outo kies die beste model wat jou rekenaar kan hardloop. Groter is meer akkuraat maar stadiger.",
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
    "Tip: use headphones. On speakers your microphone can re-hear the other people, and they get transcribed twice.":
      "Wenk: gebruik oorfone. Op luidsprekers kan jou mikrofoon die ander mense weer hoor, en hulle word twee keer getranskribeer.",
    "not detected": "nie bespeur nie",
    "Microphone switched.": "Mikrofoon geskakel.",
    "System audio switched.": "Stelselklank geskakel.",
    "Could not switch device.": "Kon nie die toestel skakel nie.",
    "Preparing transcription model": "Berei transkripsiemodel voor",
    "Transcription model ready": "Transkripsiemodel gereed",
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

    // t0-capture: live model-preparation progress, bounded failure + retry (live screen)
    "Transcription unavailable": "Transkripsie nie beskikbaar nie",
    "Loading into memory": "Laai in geheue",
    "downloaded so far": "tot dusver afgelaai",
    "{done} of {total} ({pct}%)": "{done} van {total} ({pct}%)",
    "Capturing now on this computer.": "Die klank word nou op hierdie rekenaar opgeneem.",
    "Capturing and recording now on this computer.":
      "Die klank word nou op hierdie rekenaar opgeneem en gestoor.",
    "The transcription model is still loading. The transcript fills in from the start the moment it is ready, and if you are recording, the audio is saved from the very beginning.":
      "Die transkripsiemodel laai nog. Die transkripsie vul van voor af in sodra dit gereed is, en as jy opneem, word die klank van heel voor af gestoor.",
    "Could not load the transcription model on this computer.":
      "Kon nie die transkripsiemodel op hierdie rekenaar laai nie.",
    "The download stalled. Check your connection and try again.":
      "Die aflaai het vasgeval. Kontroleer jou verbinding en probeer weer.",
    "Retry": "Probeer weer",
    "Could not retry.": "Kon nie weer probeer nie.",
    "Could not start.": "Kon nie begin nie.",
    "Your audio is still recording safely on this computer. Stop when you are done and transcribe the recording later.":
      "Jou klank word steeds veilig op hierdie rekenaar opgeneem. Stop wanneer jy klaar is en transkribeer die opname later.",
    "Recording is off, so there is no live transcript. Set up the model in Settings, then start again.":
      "Opname is af, so daar is geen lewendige transkripsie nie. Stel die model op in Instellings en begin dan weer.",

    // t0-capture: pre-start (informed consent) modal for a model that is not yet downloaded
    "Download the {label} model first?": "Laai eers die {label}-model af?",
    "About {size}, and usually a few minutes on a normal connection.":
      "Ongeveer {size}, en gewoonlik 'n paar minute op 'n normale verbinding.",
    "Capture and recording begin immediately. The transcript fills in from the start once the model is ready, and if you are recording, the audio is saved from the very beginning.":
      "Die klank word dadelik opgeneem. Die transkripsie vul van voor af in sodra die model gereed is, en as jy opneem, word die klank van heel voor af gestoor.",
    "Start instantly with a model you already have":
      "Begin dadelik met 'n model wat jy reeds het",
    "Use this": "Gebruik hierdie",
    "Proceed and download": "Gaan voort en laai af",

    // Honest quality picker (pre-meeting + live tune panel)
    "Starts instantly": "Begin dadelik",
    "Downloads first time (~{size})": "Laai die eerste keer af (~{size})",
    "Downloads first time": "Laai die eerste keer af",
    "downloads first": "laai eers af",

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
    "Finished, with a warning": "Klaar, met 'n waarskuwing",
    "Saving may not have completed": "Stoor het dalk nie voltooi nie",
    "Open folder": "Maak gids oop",
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
    "Runs on this computer using your installed model. Pick a style, or write your own instructions.":
      "Loop op hierdie rekenaar met jou geïnstalleerde model. Kies 'n styl, of skryf jou eie instruksies.",
    "Summarise": "Som op",
    "Summary": "Opsomming",
    "Latest summary": "Jongste opsomming",
    "Ran on this computer, saved next to the transcript": "Het op hierdie rekenaar geloop, gestoor langs die transkripsie",
    "Regenerate": "Genereer weer",
    "Saved as ": "Gestoor as ",
    ", next to the transcript. Nothing was sent off this computer.":
      ", langs die transkripsie. Niks is van hierdie rekenaar af gestuur nie.",
    // Summary styles
    "Summary style": "Opsommingstyl",
    "Standard (meeting minutes)": "Standaard (vergaderingnotules)",
    "Action items only": "Net aksiepunte",
    "Decisions and owners": "Besluite en eienaars",
    "Detailed notes": "Gedetailleerde notas",
    "One-paragraph summary": "Een-paragraaf-opsomming",
    "Custom instructions": "Eie instruksies",
    "Describe the summary you want. e.g. A bulleted list of risks raised, each with who raised it. Write in the second person to the team.":
      "Beskryf die opsomming wat jy wil hê. Bv. 'n Kolpuntlys van risiko's wat geopper is, elk met wie dit geopper het. Skryf in die tweede persoon aan die span.",
    "Make another summary": "Maak nog 'n opsomming",

    // History
    "Past meetings": "Vorige vergaderings",
    "Search transcripts": "Soek transkripsies",
    "New meeting": "Nuwe vergadering",
    "No meetings yet.": "Nog geen vergaderings nie.",
    "Once you transcribe a meeting, it shows up here. Nothing is uploaded; your meetings live in your data folder.":
      "Sodra jy 'n vergadering transkribeer, verskyn dit hier. Niks word opgelaai nie; jou vergaderings leef in jou data-gids.",
    "Open": "Maak oop",
    "Saved in ": "Gestoor in ",

    // Reader
    "Back to history": "Terug na geskiedenis",
    "Folder": "Gids",

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
    "Interface language": "Volksmond-toepassingstaal",
    "The language Volksmond shows you. It does not change what gets transcribed.":
      "Die taal waarin Volksmond vir jou wys. Dit verander nie wat getranskribeer word nie.",
    "Transcription": "Transkripsie",
    "Default language": "Verstektaal",
    "Used unless you change it for a meeting.": "Gebruik tensy jy dit vir 'n vergadering verander.",
    "Languages you transcribe": "Tale wat jy transkribeer",
    "Pick the languages you record in. The language you choose for a meeting picks the model; the size is chosen automatically.":
      "Kies die tale waarin jy opneem. Die taal wat jy vir 'n vergadering kies, kies die model; die grootte word outomaties gekies.",
    "Afrikaans uses Fluister, our Afrikaans-tuned model; English and other languages use standard Whisper.":
      "Afrikaans gebruik Fluister, ons Afrikaans-gestemde model; Engels en ander tale gebruik standaard Whisper.",
    "The Afrikaans-tuned Fluister model is not installed on this computer yet, so Afrikaans runs on standard Whisper for now.":
      "Die Afrikaans-gestemde Fluister-model is nog nie op hierdie rekenaar geïnstalleer nie, so Afrikaans loop voorlopig op standaard Whisper.",
    "Advanced. Auto picks the best model your hardware can run; you rarely need to change this.":
      "Gevorderd. Outo kies die beste model wat jou hardeware kan hardloop; jy hoef dit selde te verander.",
    "Auto picks the best model your hardware can run.":
      "Outo kies die beste model wat jou hardeware kan loop.",
    "Best (GPU)": "Beste (GPU)",
    "Default context, names and jargon": "Verstekkonteks, name en vakterme",
    "Applied to every meeting to help accuracy. Stored on this computer only.":
      "Toegepas op elke vergadering om akkuraatheid te help. Net op hierdie rekenaar gestoor.",
    "Save context": "Stoor konteks",
    "Summaries, run on this machine": "Opsommings, loop op hierdie masjien",
    "Run summaries on": "Loop opsommings op",
    "Summaries run on the CPU.": "Opsommings loop op die CPU.",
    "Summaries run on your NVIDIA GPU when the model fits, which is much faster. Falls back to the CPU automatically if it will not fit in graphics memory.":
      "Opsommings loop op jou NVIDIA-GPU wanneer die model pas, wat baie vinniger is. Val outomaties terug na die CPU as dit nie in die grafiese geheue pas nie.",
    "Summary model": "Opsommingsmodel",
    "Change": "Verander",
    "Choose model": "Kies model",
    "Installed": "Geïnstalleer",
    "Turn on summaries": "Skakel opsommings aan",
    "Summaries run on this computer and are free. You can switch model below any time.":
      "Opsommings loop op hierdie rekenaar en is gratis. Jy kan enige tyd hieronder van model wissel.",
    "Summary in": "Opsomming in",
    "Download a small model and Volksmond can summarise a finished transcript on this computer. Pick a size, we download it for you.":
      "Laai 'n klein model af en Volksmond kan 'n voltooide transkripsie op hierdie rekenaar opsom. Kies 'n grootte, ons laai dit vir jou af.",
    "Choose a summary model to download": "Kies 'n opsommingsmodel om af te laai",
    "Your summary model (switch or add another)": "Jou opsommingsmodel (wissel of voeg nog een by)",
    "Summaries are ready on this machine. You can switch model here, or add another.":
      "Opsommings is gereed op hierdie masjien. Jy kan hier van model wissel, of nog een byvoeg.",
    "Gemma 4 (2 billion)": "Gemma 4 (2 miljard)",
    "Gemma 4 (4 billion)": "Gemma 4 (4 miljard)",
    "Gemma 4 (12 billion)": "Gemma 4 (12 miljard)",
    "The most capable local summary. Needs a strong machine with plenty of memory, and takes a little longer.":
      "Die kragtigste plaaslike opsomming. Benodig 'n sterk masjien met baie geheue, en neem 'n bietjie langer.",
    "Smaller and faster, light on memory. Works well on most machines.":
      "Kleiner en vinniger, lig op geheue. Werk goed op die meeste masjiene.",
    "Larger and more polished. Needs more memory and a little more time.":
      "Groter en meer gepoleer. Benodig meer geheue en 'n bietjie meer tyd.",
    "Download": "Laai af",
    "Downloading": "Besig om af te laai",
    "Use": "Gebruik",
    "Could not load model options. Restart Volksmond and try again.":
      "Kon nie die modelopsies laai nie. Herbegin Volksmond en probeer weer.",
    "Try again": "Probeer weer",
    "It downloads in the background. You can continue, it keeps going, and summaries switch on when it is ready.":
      "Dit laai in die agtergrond af. Jy kan voortgaan, dit hou aan, en opsommings skakel aan wanneer dit gereed is.",
    "Choose a model size below and we download it for you. One click, no file hunting.":
      "Kies hieronder 'n modelgrootte en ons laai dit vir jou af. Een klik, geen lêersoektog nie.",
    "Loading model options...": "Laai modelopsies...",
    "Summary model ready. Summaries are on.": "Opsommingsmodel gereed. Opsommings is aan.",
    "Could not start the download.": "Kon nie die aflaai begin nie.",
    "Open data folder": "Maak data-gids oop",
    "See the transcripts and any models stored on this computer.":
      "Sien die transkripsies en enige modelle wat op hierdie rekenaar gestoor is.",
    "Data and privacy": "Data en privaatheid",
    "Save transcripts and recordings to": "Stoor transkripsies en opnames na",
    "For maximum privacy, choose a folder that a cloud provider does not sync (OneDrive, Google Drive, Dropbox, and the like).":
      "Vir maksimum privaatheid, kies 'n gids wat 'n wolkverskaffer nie sinkroniseer nie (OneDrive, Google Drive, Dropbox, en dies meer).",
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
    "Type a folder path": "Tik 'n gids-pad",
    "Type a file path": "Tik 'n lêer-pad",
    "No native file dialog is available here. Paste the full path on this computer.":
      "Geen inheemse lêer-dialoog is hier beskikbaar nie. Plak die volle pad op hierdie rekenaar.",
    "Use this path": "Gebruik hierdie pad",

    // About / credit
    "by DigiPhyte": "deur DigiPhyte",
    "About": "Oor",
    "Check for updates": "Kyk vir opdaterings",
    "Check for updates in Microsoft Store": "Kyk vir opdaterings in Microsoft Store",
    "Checking for updates": "Soek tans vir opdaterings",
    "Could not check for updates.": "Kon nie vir opdaterings soek nie.",
    "Update available": "Opdatering beskikbaar",
    "You are up to date.": "Jy is op datum.",
    "Microsoft Store normally updates Volksmond automatically. Use the button to check now.":
      "Microsoft Store werk Volksmond gewoonlik outomaties by. Gebruik die knoppie om nou te kyk.",
    "Said Fawlks-mawnt. Afrikaans for the way people actually speak.":
      "Uitgespreek Fawlks-mawnt. Afrikaans vir hoe mense werklik praat.",

    // Report a bug
    "Nothing is sent automatically. The app never phones home, you send this yourself.":
      "Niks word outomaties gestuur nie. Die program bel nooit huis toe nie, jy stuur dit self.",
    "Send it to": "Stuur dit na",
    "Copy report": "Kopieer verslag",
    "Open email": "Maak e-pos oop",
    "Close": "Maak toe",
    "A DigiPhyte product, built in South Africa. All transcription happens on this machine unless you explicitly opt in.":
      "'n DigiPhyte-produk, gebou in Suid-Afrika. Alle transkripsie gebeur op hierdie masjien tensy jy uitdruklik inteken.",

    // Voice (transcription) model download
    "Lite": "Lig (basies)",
    "Light": "Lig",
    "High quality": "Hoë gehalte",
    "The smallest and fastest, but the roughest. Only for very old or low-power computers.":
      "Die kleinste en vinnigste, maar die growwe. Net vir baie ou of lae-krag rekenaars.",
    "Light and quick, easy on memory. Good everyday accuracy on most laptops.":
      "Lig en vinnig, maklik op geheue. Goeie alledaagse akkuraatheid op die meeste skootrekenaars.",
    "A good balance of speed and accuracy on a typical computer. The usual sweet spot.":
      "'n Goeie balans van spoed en akkuraatheid op 'n tipiese rekenaar. Die gewone goeie keuse.",
    "Near the best accuracy, but lighter and faster. Great on a strong CPU or any GPU.":
      "Naby die beste akkuraatheid, maar ligter en vinniger. Wonderlik op 'n sterk SVE of enige GPU.",
    "The most accurate. Needs a graphics card (GPU) to be quick; slow on CPU alone.":
      "Die akkuraatste. Benodig 'n grafikakaart (GPU) om vinnig te wees; stadig op die SVE alleen.",
    // Model families explainer (Settings, voice model card)
    "Two model families, chosen by language": "Twee modelfamilies, gekies volgens taal",
    "Afrikaans uses Fluister, our Afrikaans-tuned model: much better on Afrikaans and the Afrikaans-English mix. It downloads automatically the first time you transcribe Afrikaans.":
      "Afrikaans gebruik Fluister, ons Afrikaans-gestemde model: baie beter op Afrikaans en die Afrikaans-Engels-mengsel. Dit laai outomaties af die eerste keer wat jy Afrikaans transkribeer.",
    "English and other languages use standard Whisper, the model you download below.":
      "Engels en ander tale gebruik standaard Whisper, die model wat jy hieronder aflaai.",
    "The size you pick (speed against accuracy) applies to whichever family your language needs.":
      "Die grootte wat jy kies (spoed teenoor akkuraatheid) geld vir watter familie jou taal ook al nodig het.",
    // Swivuriso (DSFSI / African Next Voices) family + the three-family note
    "Three model families, chosen by language": "Drie modelfamilies, gekies volgens taal",
    "South African languages model": "Suid-Afrikaanse tale-model",
    "South African languages": "Suid-Afrikaanse tale",
    "General Whisper models": "Algemene Whisper-modelle",
    "For English and other languages.": "Vir Engels en ander tale.",
    "Our Afrikaans-tuned model. Best for Afrikaans and mixed Afrikaans and English.": "Ons Afrikaans-gestemde model. Beste vir Afrikaans en gemengde Afrikaans en Engels.",
    "Other South African languages (Swivuriso)": "Ander Suid-Afrikaanse tale (Swivuriso)",
    "One model covers all seven South African languages, on auto-detect. Only High quality is available.": "Een model dek al sewe Suid-Afrikaanse tale, met outomatiese opsporing. Slegs Hoë gehalte is beskikbaar.",
    "South African languages (Swivuriso)": "Suid-Afrikaanse tale (Swivuriso)",
    "Beta": "Beta",
    "Fluister, our Afrikaans-tuned model: best for Afrikaans and mixed Afrikaans and English meetings. It downloads automatically the first time you transcribe Afrikaans.":
      "Fluister, ons Afrikaans-gestemde model: die beste vir Afrikaans en vergaderings wat Afrikaans en Engels meng. Dit laai outomaties af die eerste keer wat jy Afrikaans transkribeer.",
    "Swivuriso, by African Next Voices (DSFSI): one model for seven South African languages (isiZulu, isiXhosa, Sesotho, Setswana, Xitsonga, isiNdebele, Tshivenda). Beta.":
      "Swivuriso, deur African Next Voices (DSFSI): een model vir sewe Suid-Afrikaanse tale (isiZulu, isiXhosa, Sesotho, Setswana, Xitsonga, isiNdebele, Tshivenda). Beta.",
    "One model covers all seven. It runs on auto-detect.": "Een model dek al sewe. Dit loop op outomatiese opsporing.",
    "Not installed yet. Download it now, or it downloads automatically the first time you pick one of these languages.": "Nog nie geïnstalleer nie. Laai dit nou af, of dit laai outomaties af die eerste keer wat jy een van hierdie tale kies.",
    "Model by DSFSI, African Next Voices. MIT licence.": "Model deur DSFSI, African Next Voices. MIT-lisensie.",
    "The Swivuriso model for South African languages is not installed on this computer yet, so this runs on standard Whisper for now.":
      "Die Swivuriso-model vir Suid-Afrikaanse tale is nog nie op hierdie rekenaar geïnstalleer nie, so dit loop voorlopig op standaard Whisper.",
    "South African languages use Swivuriso, a model by African Next Voices (DSFSI). The size is chosen automatically for your computer.":
      "Suid-Afrikaanse tale gebruik Swivuriso, 'n model deur African Next Voices (DSFSI). Die grootte word outomaties vir jou rekenaar gekies.",
    "Best for Afrikaans and mixed Afrikaans and English meetings. The size is chosen automatically for your computer.":
      "Die beste vir Afrikaans en vergaderings wat Afrikaans en Engels meng. Die grootte word outomaties vir jou rekenaar gekies.",
    "Afrikaans uses Fluister, our Afrikaans-tuned model; the South African languages use Swivuriso (beta); English and other languages use standard Whisper.":
      "Afrikaans gebruik Fluister, ons Afrikaans-gestemde model; die Suid-Afrikaanse tale gebruik Swivuriso (beta); Engels en ander tale gebruik standaard Whisper.",
    // "More languages" picker (pre-meeting) + the world-language list
    "More languages": "Meer tale",
    "Any South African language": "Enige Suid-Afrikaanse taal",
    "World languages (Whisper)": "Wêreldtale (Whisper)",
    "uses standard Whisper. The size is chosen automatically for your computer.":
      "gebruik standaard Whisper. Die grootte word outomaties vir jou rekenaar gekies.",
    "German": "Duits",
    "French": "Frans",
    "Spanish": "Spaans",
    "Portuguese": "Portugees",
    "Italian": "Italiaans",
    "Dutch": "Nederlands",
    "Mandarin": "Mandaryns",
    "Arabic": "Arabies",
    "Hindi": "Hindi",
    "Russian": "Russies",
    "Japanese": "Japannees",
    "Korean": "Koreaans",
    "Polish": "Pools",
    "Turkish": "Turks",
    "Swedish": "Sweeds",
    "Norwegian": "Noors",
    "Danish": "Deens",
    "Greek": "Grieks",
    // Afrikaans (Fluister) model update panel
    "Afrikaans model (Fluister)": "Afrikaanse model (Fluister)",
    "Checking": "Soek tans",
    "Up to date": "Op datum",
    "Update": "Opdateer",
    "Updating": "Opdateer tans",
    "Not installed yet. The Afrikaans model downloads automatically the first time you transcribe Afrikaans.":
      "Nog nie geïnstalleer nie. Die Afrikaanse model laai outomaties af die eerste keer wat jy Afrikaans transkribeer.",
    "Your Afrikaans model is up to date.": "Jou Afrikaanse model is op datum.",
    "Could not check for model updates.": "Kon nie vir modelopdaterings soek nie.",
    "Could not start the update.": "Kon nie die opdatering begin nie.",
    "Needs a graphics card (GPU). Choose another for this computer.":
      "Benodig 'n grafikakaart (GPU). Kies 'n ander een vir hierdie rekenaar.",
    "Transcription model ready.": "Transkripsiemodel gereed.",
    "Transcription model, on this machine": "Transkripsiemodel, op hierdie masjien",
    "Download or switch model": "Laai af of wissel model",
    "Volksmond transcribes on this computer. Download the model that suits your machine; the recommended one is marked. Bigger is more accurate, but slower and larger to download. Remove any you no longer need to free space.":
      "Volksmond transkribeer op hierdie rekenaar. Laai die model af wat by jou masjien pas; die aanbevole een is gemerk. Groter is meer akkuraat, maar stadiger en groter om af te laai. Verwyder enige wat jy nie meer nodig het nie om spasie vry te maak.",
    "Remove": "Verwyder",
    "Remove this model?": "Verwyder hierdie model?",
    "Remove this transcription model from your computer? You can download it again later.":
      "Verwyder hierdie transkripsiemodel van jou rekenaar? Jy kan dit later weer aflaai.",
    "Remove this summary model from your computer? You can download it again later.":
      "Verwyder hierdie opsommingsmodel van jou rekenaar? Jy kan dit later weer aflaai.",
    "Model removed.": "Model verwyder.",
    "Could not remove.": "Kon nie verwyder nie.",
    "A transcription session is running. Stop it before removing a transcription model.":
      "'n Transkripsiesessie loop tans. Stop dit voordat jy 'n transkripsiemodel verwyder.",
    "Are you sure?": "Is jy seker?",

    // First-run: save location (was previously untranslated)
    "Setup, where to save": "Opstelling, waar om te stoor",
    "Where should your transcripts go?": "Waarheen moet jou transkripsies gaan?",
    "Every meeting is saved as a Markdown file. Pick a folder you can find later, or keep the default.":
      "Elke vergadering word as 'n Markdown-lêer gestoor. Kies 'n gids wat jy later kan kry, of hou die verstek.",
    "Your folder": "Jou gids",
    "Default folder (per user, on this computer)": "Verstekgids (per gebruiker, op hierdie rekenaar)",
    "Choose a different folder": "Kies 'n ander gids",
    "Choose another folder": "Kies 'n ander gids",

    // Quality selector (meeting screen) + model storage
    "Not downloaded yet. Click to download.": "Nog nie afgelaai nie. Klik om af te laai.",
    "Downloading the model. You can begin once it is ready.":
      "Laai die model af. Jy kan begin sodra dit gereed is.",
    "Where models are stored": "Waar modelle gestoor word",
    "Where summary models are stored": "Waar opsommingsmodelle gestoor word",
    "You can delete these folders by hand to free space if you ever need to.":
      "Jy kan hierdie gidse met die hand uitvee om spasie vry te maak as jy ooit moet.",
    "You can delete these files by hand to free space if you ever need to.":
      "Jy kan hierdie lêers met die hand uitvee om spasie vry te maak as jy ooit moet.",

    // Licence / upgrade
    "Coming soon": "Binnekort beskikbaar",

    // NVIDIA CUDA (optional GPU acceleration)
    "Setup, GPU acceleration": "Opstelling, GPU-versnelling",
    "Use your NVIDIA graphics card?": "Gebruik jou NVIDIA-grafikakaart?",
    "We found an NVIDIA graphics card. You can download the NVIDIA CUDA libraries so the Best model runs on your GPU, which is much faster than the CPU. This is optional and NVIDIA only; without it everything still works on the CPU. AMD and Intel graphics are not supported by the engine.":
      "Ons het 'n NVIDIA-grafikakaart gevind. Jy kan die NVIDIA CUDA-biblioteke aflaai sodat die Beste model op jou GPU loop, wat baie vinniger as die SVE is. Dit is opsioneel en slegs vir NVIDIA; daarsonder werk alles steeds op die SVE. AMD- en Intel-grafika word nie deur die enjin ondersteun nie.",
    "It is a large download (about 1.5 GB). You can skip this and set it up later in Settings. After it downloads, restart Volksmond to use your GPU.":
      "Dit is 'n groot aflaai (omtrent 1.5 GB). Jy kan dit oorslaan en later in Instellings opstel. Nadat dit afgelaai is, herbegin Volksmond om jou GPU te gebruik.",
    "NVIDIA GPU acceleration": "NVIDIA GPU-versnelling",
    "Detected": "Bespeur",
    "Download the NVIDIA CUDA libraries to run the Best model on your GPU, much faster than the CPU. NVIDIA only.":
      "Laai die NVIDIA CUDA-biblioteke af om die Beste model op jou GPU te loop, baie vinniger as die SVE. Slegs NVIDIA.",
    "An NVIDIA graphics card was detected. Download the NVIDIA CUDA libraries (about 1.5 GB) to run the Best model on your GPU, much faster than the CPU. NVIDIA only.":
      "'n NVIDIA-grafikakaart is bespeur. Laai die NVIDIA CUDA-biblioteke af (omtrent 1.5 GB) om die Beste model op jou GPU te loop, baie vinniger as die SVE. Slegs NVIDIA.",
    "Active": "Aktief",
    "Restart to use": "Herbegin om te gebruik",
    "Downloaded. Close and reopen Volksmond to start using your GPU.":
      "Afgelaai. Maak Volksmond toe en weer oop om jou GPU te begin gebruik.",
    "GPU acceleration (NVIDIA only)": "GPU-versnelling (slegs NVIDIA)",
    "Run the Best model on your NVIDIA graphics card instead of the CPU. Optional, and NVIDIA only; AMD and Intel graphics use the CPU.":
      "Loop die Beste model op jou NVIDIA-grafikakaart in plaas van die SVE. Opsioneel, en slegs NVIDIA; AMD- en Intel-grafika gebruik die SVE.",
    "Where the CUDA libraries are stored": "Waar die CUDA-biblioteke gestoor word",
    "Remove CUDA libraries?": "Verwyder CUDA-biblioteke?",
    "Remove the NVIDIA CUDA libraries from your computer? Transcription falls back to the CPU. You can download them again later.":
      "Verwyder die NVIDIA CUDA-biblioteke van jou rekenaar? Transkripsie val terug na die SVE. Jy kan dit later weer aflaai.",
    "CUDA libraries ready. Restart Volksmond to use your GPU.":
      "CUDA-biblioteke gereed. Herbegin Volksmond om jou GPU te gebruik.",
    "CUDA libraries removed.": "CUDA-biblioteke verwyder.",
    "GPU ready. No restart needed.": "GPU gereed. Geen herbegin nodig nie.",
    "Check GPU": "Toets GPU",
    "GPU is working. It will be used for transcription.": "GPU werk. Dit sal vir transkripsie gebruik word.",
    "Could not check the GPU.": "Kon nie die GPU toets nie.",
    "CPU": "SVE",
    "Transcript": "Transkripsie",
    "Auto mic volume": "Outomatiese mikrofoonvolume",
    "Automatically boosts a quiet microphone to a healthy level, the way Meet and Teams do. Leave it on unless your microphone levels are already set exactly how you want them.":
      "Versterk 'n sagte mikrofoon outomaties tot 'n gesonde vlak, soos Meet en Teams dit doen. Los dit aan tensy jou mikrofoonvlakke reeds presies reg gestel is.",
    "Cancel echo live": "Kanselleer eggo regstreeks",
    "beta": "beta",
    "Remove the other side's voice that your speakers leak into your microphone, live as the meeting happens. Best on speakers when you are mostly listening; it can blur your words during heavy crosstalk, and does nothing on headphones.": "Verwyder die ander kant se stem wat jou luidsprekers in jou mikrofoon laat lek, regstreeks soos die vergadering plaasvind. Beste op luidsprekers wanneer jy meestal luister; dit kan jou woorde vertroebel tydens baie gelyktydige gepratery, en doen niks op oorfone nie.",
    "Cancel speaker echo": "Kanselleer luidspreker-eggo",
    "Off by default. When you re-transcribe a recording, remove the other side's voice that your microphone re-heard through the speakers. Best when you are mostly listening (a video or a one-sided talk). It can blur your own words when you and the other side talk over each other, so leave it off for normal back-and-forth meetings. No effect on headphones.": "Standaard af. Wanneer jy 'n opname hertranskribeer, verwyder die ander kant se stem wat jou mikrofoon deur die luidsprekers weer gehoor het. Beste wanneer jy meestal luister ('n video of 'n eensydige praatjie). Dit kan jou eie woorde vertroebel wanneer jy en die ander kant gelyktydig praat, so los dit af vir gewone heen-en-weer-vergaderings. Geen effek op oorfone nie.",
    "Echo cancellation on.": "Eggo-kansellering aan.",
    "Echo cancellation off.": "Eggo-kansellering af.",
    "Could not change echo cancellation.": "Kon nie eggo-kansellering verander nie.",
    "Echo cancellation changed for this meeting, but the choice could not be saved as your default.": "Eggo-kansellering is vir hierdie vergadering verander, maar die keuse kon nie as jou verstek gestoor word nie.",

    // Stereo interview mode (upload option: one speaker per channel)
    "Stereo interview mode": "Stereo-onderhoudmodus",
    "For phone recordings where the two speakers sit in the left and right channels (e.g. Samsung Interview mode). Transcribes each side separately, labelled Speaker L and Speaker R. A mono file is transcribed as a single track.":
      "Vir foonopnames waar die twee sprekers in die linker- en regterkanale sit (bv. Samsung se onderhoudmodus). Transkribeer elke kant apart, gemerk Spreker L en Spreker R. 'n Mono-lêer word as een enkele baan getranskribeer.",
    "File is mono, transcribed as a single track": "Die lêer is mono en is as een enkele baan getranskribeer",
    // Quiet-channel auto boost notice; app.js trNotice() re-attaches the dynamic "(+13.6 dB)" tail.
    "Quiet audio boosted for transcription": "Stil klank is vir transkripsie versterk",
    "[Speaker L]": "[Spreker L]",
    "[Speaker R]": "[Spreker R]",

    "Recorded": "Opgeneem",
    "Transcribing": "Transkribeer tans",
    "Summarising": "Som tans op",
    "Re-transcribe": "Hertranskribeer",

    // Sidebar return pill while a session runs on another screen
    "Return to meeting": "Terug na vergadering",

    // Meeting notes (live panel, history pill, and the reader's editable My notes tab)
    "Notes": "Notas",
    "My notes": "My notas",
    "Your notes": "Jou notas",
    "Open my notes": "Maak my notas oop",
    "saved on this computer": "op hierdie rekenaar gestoor",
    "Summarise with these notes": "Som op met hierdie notas",
    "Update summary with these notes": "Werk die opsomming by met hierdie notas",
    "Add your own notes for this meeting: decisions, names, to-dos, anything you did not catch during the call. Saved with this meeting on your computer.":
      "Voeg jou eie notas vir hierdie vergadering by: besluite, name, dinge om te doen, enigiets wat jy tydens die oproep gemis het. Word saam met hierdie vergadering op jou rekenaar gestoor.",
    "Your notes are never mixed into the transcript. They stay on this computer, and you decide whether a summary uses them.":
      "Jou notas word nooit met die transkripsie vermeng nie. Hulle bly op hierdie rekenaar, en jy besluit of 'n opsomming hulle gebruik.",
    "Jot notes as the meeting goes: decisions, names, to-dos. Saved with this meeting on your computer. When you summarise, you choose whether to fold them in.":
      "Skryf notas terwyl die vergadering aangaan: besluite, name, dinge om te doen. Word saam met hierdie vergadering op jou rekenaar gestoor. Wanneer jy opsom, kies jy of jy hulle wil insluit.",
    "Include my notes in this summary": "Sluit my notas by hierdie opsomming in",
    "Your notes stay saved with the meeting either way. This tells the summary to treat them as your own record.":
      "Jou notas bly in elk geval saam met die vergadering gestoor. Dit sê vir die opsomming om hulle as jou eie rekord te behandel.",

    // Calendar seeding (local Outlook, Business) and the upgrade view
    "Pull from Outlook calendar": "Trek uit Outlook-kalender",
    "Reads your current meeting on this computer. Nothing is sent anywhere.":
      "Lees jou huidige vergadering op hierdie rekenaar. Niks word enige plek heen gestuur nie.",
    "No current or upcoming meeting found in Outlook.": "Geen huidige of komende vergadering in Outlook gevind nie.",
    "Added names from your calendar.": "Name uit jou kalender bygevoeg.",
    "Calendar meeting found; those names are already added.": "Kalendervergadering gevind; daardie name is reeds bygevoeg.",
    "Pulling from your calendar is a business feature.": "Om name uit jou kalender te trek is 'n sakekenmerk.",
    "A meeting is starting": "'n Vergadering begin nou",
    "Start transcribing it? Names from the meeting are added automatically.": "Transkribeer dit? Name uit die vergadering word outomaties bygevoeg.",
    "Start transcribing": "Begin transkribeer",
    "Not now": "Nie nou nie",
    "Dismiss": "Maak toe",
    "Windows notifications": "Windows-kennisgewings",
    "Let Volksmond send a Windows notification when it needs to tell you something while its window is hidden behind your meeting. Nothing is sent anywhere; the message appears on this computer only.":
      "Laat Volksmond 'n Windows-kennisgewing stuur wanneer dit vir jou iets moet sê terwyl sy venster agter jou vergadering weggesteek is. Niks word enige plek heen gestuur nie; die boodskap verskyn net op hierdie rekenaar.",
    // Long-silence warning during a live session (the banner, its buttons, the setting)
    "Nothing heard for": "Niks gehoor vir",
    "minutes": "minute",
    "Volksmond is still recording, but both the microphone and the system audio have been silent. Check your device, or stop and save.":
      "Volksmond neem steeds op, maar sowel die mikrofoon as die stelselklank was stil. Kyk na jou toestel, of stop en stoor.",
    "Keep recording": "Hou aan opneem",
    "Stop warning me this session": "Moenie my weer in hierdie sessie waarsku nie",
    "No more silence warnings this session.": "Geen stilte-waarskuwings meer in hierdie sessie nie.",
    "Still recording. We will tell you again if it stays silent.": "Neem steeds op. Ons sê weer as dit stil bly.",
    // "Model struggling to keep up" nudge + mid-session "Record from here"
    "Struggling to keep up": "Sukkel om by te hou",
    "Volksmond switched to a lighter, faster model to stay live, so this part may be less accurate. Record now and re-transcribe at full accuracy afterward.":
      "Volksmond het na 'n ligter, vinniger model oorgeskakel om lewendig te bly, so hierdie deel is dalk minder akkuraat. Neem nou op en hertranskribeer daarna teen volle akkuraatheid.",
    "Volksmond switched to a lighter, faster model to stay live, so this part may be less accurate. Your recording can be re-transcribed at full accuracy afterward.":
      "Volksmond het na 'n ligter, vinniger model oorgeskakel om lewendig te bly, so hierdie deel is dalk minder akkuraat. Jou opname kan daarna teen volle akkuraatheid hertranskribeer word.",
    "Record from here": "Neem van hier af op",
    "Keep going": "Gaan voort",
    "Don't warn again": "Moenie weer waarsku nie",
    "Recording from here. Earlier audio is not saved.": "Neem van hier af op. Vroeëre klank word nie gestoor nie.",
    "Could not start recording.": "Kon nie opname begin nie.",
    "Won't warn again": "Sal nie weer waarsku nie",
    "Warn me when the model can't keep up": "Waarsku my wanneer die model nie kan byhou nie",
    "On a slower computer, Volksmond drops to a lighter, faster model to stay live. When it does, it tells you so you can record and re-transcribe at full accuracy afterward.":
      "Op 'n stadiger rekenaar val Volksmond terug na 'n ligter, vinniger model om lewendig te bly. Wanneer dit gebeur, sê dit vir jou sodat jy kan opneem en daarna teen volle akkuraatheid kan hertranskribeer.",
    "Warn me about long silences": "Waarsku my oor lang stiltes",
    "If nothing at all reaches Volksmond during a meeting, neither your microphone nor the system audio, it warns you instead of quietly recording an hour of nothing. Useful when Windows moves your microphone to another device.":
      "As niks hoegenaamd Volksmond bereik tydens 'n vergadering nie, nie jou mikrofoon of die stelselklank nie, waarsku dit jou eerder as om stil-stil 'n uur van niks op te neem. Handig wanneer Windows jou mikrofoon na 'n ander toestel skuif.",
    "After 3 minutes": "Na 3 minute",
    "After 5 minutes": "Na 5 minute",
    "After 10 minutes": "Na 10 minute",
    "After 15 minutes": "Na 15 minute",
    "Show a reminder card when a meeting starts": "Wys 'n herinneringskaart wanneer 'n vergadering begin",
    "While Volksmond is open, it checks your Outlook calendar on this computer and shows a reminder card, inside the app, offering to start transcribing when a meeting begins. Windows notifications are switched separately, above. Nothing is sent anywhere, and it never starts on its own.":
      "Terwyl Volksmond oop is, kyk dit na jou Outlook-kalender op hierdie rekenaar en wys 'n herinneringskaart, binne die program, wat aanbied om te begin transkribeer wanneer 'n vergadering begin. Windows-kennisgewings word afsonderlik aan- en afgeskakel, hierbo. Niks word enige plek heen gestuur nie, en dit begin nooit vanself nie.",
    "Pull attendee names from your Outlook calendar": "Trek bywoners se name uit jou Outlook-kalender",
    "Priority email support": "Voorkeur-e-posondersteuning",
    "Coming soon": "Binnekort",
    "Premium South African transcription models": "Premium Suid-Afrikaanse transkripsiemodelle",
    "An optional online tier that runs our most accurate South African models on DigiPhyte's own hardware in South Africa. Your audio would leave this computer, but it stays in the country on hardware we control, so it remains POPIA-friendly. The local, offline transcription always stays free and is never replaced.":
      "'n Opsionele aanlyn vlak wat ons akkuraatste Suid-Afrikaanse modelle op DigiPhyte se eie hardeware in Suid-Afrika laat loop. Jou klank sal hierdie rekenaar verlaat, maar dit bly in die land op hardeware wat ons beheer, so dit bly POPIA-vriendelik. Die plaaslike, vanlyn transkripsie bly altyd gratis en word nooit vervang nie.",
    "Personal use is the real thing: unlimited live transcription and local summaries, on this machine, forever. A business licence covers commercial and team use, and unlocks the extras for professional work.":
      "Persoonlike gebruik is die egte ding: onbeperkte lewendige transkripsie en plaaslike opsommings, op hierdie masjien, vir altyd. 'n Sakelisensie dek kommersiële en spangebruik, en ontsluit die ekstras vir professionele werk.",
    "Transcribe this recording?": "Transkribeer hierdie opname?",
    "Re-transcribe from the recording?": "Hertranskribeer vanaf die opname?",
    "Transcribes both sides (you and the other person) as separate speakers. Pick the language and model for this pass below.": "Transkribeer albei kante (jy en die ander persoon) as aparte sprekers. Kies hieronder die taal en model vir hierdie deurloop.",
    "Language switched.": "Taal geskakel.",
    "Model switched.": "Model geskakel.",
    "Could not change the settings.": "Kon nie die instellings verander nie.",
    "Re-transcribes both sides from the saved audio and replaces the current transcript. The audio is kept. Use it for a cleaner pass than the live one.": "Hertranskribeer albei kante vanaf die gestoorde klank en vervang die huidige transkripsie. Die klank word behou. Gebruik dit vir 'n skoner weergawe as die lewendige een.",
    "Skip setup for now": "Slaan opstelling vir eers oor",
    "Run on": "Verwerk op",
    "On the GPU, Volksmond runs the Best model. The Quality choice applies on CPU.": "Op die GPU loop Volksmond die Beste model. Die Gehalte-keuse geld op die SVE.",
    "Running on the CPU. The Quality choice above applies.": "Loop op die SVE. Die Gehalte-keuse hierbo geld.",

    // Pre-meeting quality hint
    "Best needs a graphics card (GPU), which this computer does not have. Choose Balanced or Fast.":
      "Beste benodig 'n grafikakaart (GPU), wat hierdie rekenaar nie het nie. Kies Gebalanseerd of Vinnig.",
    "This quality downloads the first time you use it.":
      "Hierdie gehalte laai die eerste keer wat jy dit gebruik af.",

    // Starting screen (immediate feedback while the model loads)
    "Starting": "Begin tans",
    "Preparing": "Berei voor",
    "Not started": "Nie begin nie",
    "Stopped": "Gestop",
    "Could not start": "Kon nie begin nie",
    "Set up models": "Stel modelle op",
    "Loading the transcription model on your computer. The first time you use a quality level can take a moment, and if that model still needs downloading it can take a few minutes.":
      "Laai die transkripsiemodel op jou rekenaar. Die eerste keer wat jy 'n gehaltevlak gebruik kan 'n oomblik neem, en as daardie model nog afgelaai moet word kan dit 'n paar minute neem.",
    "You can keep this open. It switches to the transcript by itself.":
      "Jy kan dit oop hou. Dit wissel vanself na die transkripsie.",
    // Live path uses t0-capture (no full-screen spinner), but keep these translated in case the
    // brief starting screen is ever shown for a live session.
    "Opening your microphone and system audio and starting to capture. This is quick.":
      "Maak jou mikrofoon en stelselklank oop en begin opneem. Dit is vinnig.",
    "The live screen opens by itself. The transcript fills in from the start once the model is ready.":
      "Die lewendige skerm maak vanself oop. Die transkripsie vul van voor af in sodra die model gereed is.",
  },
};
