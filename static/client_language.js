(function () {
  const STORAGE_KEY = "apexClientLanguage";
  const dictionaries = {
    es: {
      language_label: "Idioma",
      agency: "Agencia",
      your_questionnaires: "Sus cuestionarios",
      no_cases_assigned: "Aún no se le ha asignado ningún caso.",
      continue: "Continuar",
      start_questionnaire: "Iniciar cuestionario",
      questionnaire: "Cuestionario",
      questionnaire_lower: "cuestionario",
      complete: "completo",
      questionnaire_not_configured: "Este cuestionario aún no ha sido configurado.",
      welcome: "Bienvenida",
      documents: "Documentos",
      before_begin: "Antes de comenzar",
      welcome_to_your: "Bienvenido a su",
      intro_save: "Responda cada pregunta lo más completo posible. Puede guardar y salir en cualquier momento, y volver más tarde para continuar.",
      intro_na: "Si una pregunta no aplica a usted, escriba",
      intro_review: "Cuando termine, su agencia revisará sus respuestas antes de preparar el formulario.",
      intro_documents: "Al final del cuestionario podrá subir los documentos que su agencia solicitó.",
      start_or_continue: "Iniciar o continuar",
      exit: "Salir",
      final_step: "Paso final",
      upload_requested_documents: "Subir documentos solicitados",
      upload_multiple: "Puede subir varios archivos ahora y volver más tarde para agregar más documentos desde este mismo lugar.",
      uploaded_documents: "Documentos subidos",
      no_documents_uploaded: "Aún no hay documentos subidos.",
      back: "Atrás",
      upload_documents: "Subir documentos",
      save_and_exit: "Guardar y salir",
      question: "Pregunta",
      of: "de",
      yes: "Sí",
      no: "No",
      next_question: "Siguiente pregunta",
      answer_later: "Responder después",
      save_progress_exit: "Guardar progreso y salir",
      "Created": "Creado",
      "Client Questionnaire Started": "Cuestionario iniciado",
      "Waiting for Client": "Esperando al cliente",
      "Ready for Review": "Listo para revisión",
      "In Preparation": "En preparación",
      "Generated": "Generado",
      "Completed": "Completado"
    },
    ht: {
      language_label: "Lang",
      agency: "Ajans",
      your_questionnaires: "Kesyonè ou yo",
      no_cases_assigned: "Pa gen dosye ki asiyen pou ou ankò.",
      continue: "Kontinye",
      start_questionnaire: "Kòmanse kesyonè a",
      questionnaire: "Kesyonè",
      questionnaire_lower: "kesyonè",
      complete: "fini",
      questionnaire_not_configured: "Kesyonè sa a poko konfigire.",
      welcome: "Akèy",
      documents: "Dokiman",
      before_begin: "Anvan ou kòmanse",
      welcome_to_your: "Byenveni nan",
      intro_save: "Reponn chak kesyon pi konplè ou kapab. Ou ka sove epi sòti nenpòt lè, epi retounen pita pou kontinye.",
      intro_na: "Si yon kesyon pa aplike pou ou, ekri",
      intro_review: "Lè ou fini, ajans ou a ap revize repons ou yo anvan li prepare fòm nan.",
      intro_documents: "Nan fen kesyonè a, ou pral kapab telechaje dokiman ajans ou a mande yo.",
      start_or_continue: "Kòmanse oswa kontinye",
      exit: "Sòti",
      final_step: "Dènye etap",
      upload_requested_documents: "Telechaje dokiman yo mande yo",
      upload_multiple: "Ou ka telechaje plizyè fichye kounye a, epi ou ka retounen pita pou ajoute plis dokiman menm kote sa a.",
      uploaded_documents: "Dokiman ki telechaje",
      no_documents_uploaded: "Pa gen dokiman ki telechaje ankò.",
      back: "Retounen",
      upload_documents: "Telechaje dokiman",
      save_and_exit: "Sove epi sòti",
      question: "Kesyon",
      of: "sou",
      yes: "Wi",
      no: "Non",
      next_question: "Pwochen kesyon",
      answer_later: "Reponn pita",
      save_progress_exit: "Sove pwogrè epi sòti",
      "Created": "Kreye",
      "Client Questionnaire Started": "Kesyonè kòmanse",
      "Waiting for Client": "Ap tann kliyan an",
      "Ready for Review": "Pare pou revizyon",
      "In Preparation": "An preparasyon",
      "Generated": "Jenere",
      "Completed": "Fini"
    }
  };

  const promptTranslations = {
    es: {
      "Are you a Male?": "¿Es hombre?",
      "Are you a Female?": "¿Es mujer?",
      "Are you single? (Never Married)": "¿Está soltero/a? (Nunca casado/a)",
      "Are you married?": "¿Está casado/a?",
      "Are you divorced?": "¿Está divorciado/a?",
      "Are you widowed?": "¿Es viudo/a?",
      "Have you previously filed Form I-765?": "¿Ha presentado anteriormente el Formulario I-765?",
      "Applicant's First Name": "Nombre del solicitante",
      "Applicant's Middle Name": "Segundo nombre del solicitante",
      "Applicant's Last Name(s)": "Apellido(s) del solicitante",
      "Provide your Social Security number (SSN) (if known).": "Indique su número de Seguro Social (SSN), si lo conoce.",
      "Alien Registration Number (A-Number) (if any)": "Número de Registro de Extranjero (A-Number), si tiene",
      "Form I-94 Arrival-Departure Record Number (if any)": "Número de registro de entrada/salida del Formulario I-94, si tiene"
    },
    ht: {
      "Are you a Male?": "Èske ou se gason?",
      "Are you a Female?": "Èske ou se fi?",
      "Are you single? (Never Married)": "Èske ou selibatè? (Pa janm marye)",
      "Are you married?": "Èske ou marye?",
      "Are you divorced?": "Èske ou divòse?",
      "Are you widowed?": "Èske mari/madanm ou mouri?",
      "Have you previously filed Form I-765?": "Èske ou te deja soumèt Fòm I-765?",
      "Applicant's First Name": "Non aplikan an",
      "Applicant's Middle Name": "Dezyèm non aplikan an",
      "Applicant's Last Name(s)": "Siyati aplikan an",
      "Provide your Social Security number (SSN) (if known).": "Bay nimewo Sekirite Sosyal ou (SSN), si ou konnen li.",
      "Alien Registration Number (A-Number) (if any)": "Nimewo anrejistreman etranje (A-Number), si genyen",
      "Form I-94 Arrival-Departure Record Number (if any)": "Nimewo dosye antre-sòti Fòm I-94, si genyen"
    }
  };

  function textFor(language, key) {
    if (language === "en") {
      return null;
    }
    return dictionaries[language]?.[key] || null;
  }

  function promptFor(language, text) {
    if (language === "en") {
      return null;
    }
    return promptTranslations[language]?.[text.trim()] || textFor(language, text.trim());
  }

  function applyLanguage(language) {
    document.documentElement.lang = language === "ht" ? "ht" : language;
    document.querySelectorAll("[data-i18n]").forEach((element) => {
      if (!element.dataset.originalText) {
        element.dataset.originalText = element.textContent;
      }
      element.textContent = textFor(language, element.dataset.i18n) || element.dataset.originalText;
    });
    document.querySelectorAll("[data-i18n-text]").forEach((element) => {
      if (!element.dataset.originalText) {
        element.dataset.originalText = element.textContent;
      }
      element.textContent = promptFor(language, element.dataset.i18nText) || element.dataset.originalText;
    });
  }

  const select = document.getElementById("client-language-select");
  const savedLanguage = localStorage.getItem(STORAGE_KEY) || "en";
  if (select) {
    select.value = savedLanguage;
    select.addEventListener("change", () => {
      localStorage.setItem(STORAGE_KEY, select.value);
      applyLanguage(select.value);
    });
  }
  applyLanguage(savedLanguage);
})();
