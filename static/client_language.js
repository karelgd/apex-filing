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
      "What is your First Name?": "Cual es su primer nombre?",
      "What is your Middle Name? (or N/A)": "Cual es su segundo nombre? (o N/A)",
      "What is your Last Name?": "Cual es su apellido?",
      "What is your Last Name(s)?": "Cuales son sus apellidos?",
      "Is this the first time you apply for work authorization under this category?": "Es la primera vez que solicita autorizacion de empleo bajo esta categoria?",
      "Are you applying because you lost or damage your work permit card?": "Esta solicitando porque perdio o se dano su permiso de trabajo?",
      "Are you trying to renew your work authorization?": "Esta tratando de renovar su autorizacion de empleo?",
      "What is your current address?": "Cual es su direccion actual?",
      "What is your phone number?": "Cual es su numero de telefono?",
      "What is your email address?": "Cual es su correo electronico?",
      "What is your date of birth?": "Cual es su fecha de nacimiento?",
      "What is your country of birth?": "Cual es su pais de nacimiento?",
      "What is your country of citizenship?": "Cual es su pais de ciudadania?",
      "What is your A-Number?": "Cual es su A-Number?",
      "What is your Social Security Number?": "Cual es su numero de Seguro Social?",
      "What is your I-94 number?": "Cual es su numero I-94?",
      "What is your i94 number?": "Cual es su numero I-94?",
      "What is your I94 number?": "Cual es su numero I-94?",
      "Do you have a USCIS Online Account Number?": "Tiene un numero de cuenta en linea de USCIS?",
      "Have you filed for work authorization before?": "Ha solicitado autorizacion de empleo anteriormente?",
      "When did you Last Enter the United States?": "Cuando fue la ultima vez que entro a los Estados Unidos?",
      "When did you last enter the United States?": "Cuando fue la ultima vez que entro a los Estados Unidos?",
      "What Country Issued your Passport?": "Que pais emitio su pasaporte?",
      "What country issued your passport?": "Que pais emitio su pasaporte?",
      "What was your place of entry into the USA? (Ex: El Paso, TX)": "Cual fue su lugar de entrada a los Estados Unidos? (Ej.: El Paso, TX)",
      "What was your Status when you last entered the United States?": "Cual era su estatus cuando entro por ultima vez a los Estados Unidos?",
      "What was your status when you last entered the United States?": "Cual era su estatus cuando entro por ultima vez a los Estados Unidos?",
      "Do you have any criminal history?": "Tiene algun antecedente penal?",
      "Select YES here to continue.": "Seleccione SI aqui para continuar.",
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
      "What is your First Name?": "Ki prenon ou?",
      "What is your Middle Name? (or N/A)": "Ki dezyem non ou? (oswa N/A)",
      "What is your Last Name?": "Ki siyati ou?",
      "What is your Last Name(s)?": "Ki siyati ou yo?",
      "Is this the first time you apply for work authorization under this category?": "Eske se premye fwa ou aplike pou otorizasyon travay anba kategori sa a?",
      "Are you applying because you lost or damage your work permit card?": "Eske w ap aplike paske ou pedi oswa domaje kat travay ou?",
      "Are you trying to renew your work authorization?": "Eske w ap eseye renouvle otorizasyon travay ou?",
      "What is your current address?": "Ki adres ou kounye a?",
      "What is your phone number?": "Ki nimewo telefon ou?",
      "What is your email address?": "Ki imel ou?",
      "What is your date of birth?": "Ki dat nesans ou?",
      "What is your country of birth?": "Nan ki peyi ou fet?",
      "What is your country of citizenship?": "Ki peyi sitwayente ou?",
      "What is your A-Number?": "Ki A-Number ou?",
      "What is your Social Security Number?": "Ki nimewo Sekirite Sosyal ou?",
      "What is your I-94 number?": "Ki nimewo I-94 ou?",
      "What is your i94 number?": "Ki nimewo I-94 ou?",
      "What is your I94 number?": "Ki nimewo I-94 ou?",
      "Do you have a USCIS Online Account Number?": "Eske ou gen yon nimewo kont sou entenet USCIS?",
      "Have you filed for work authorization before?": "Eske ou te deja aplike pou otorizasyon travay?",
      "When did you Last Enter the United States?": "Ki le ou te antre Ozetazini denye fwa?",
      "When did you last enter the United States?": "Ki le ou te antre Ozetazini denye fwa?",
      "What Country Issued your Passport?": "Ki peyi ki te bay paspo ou?",
      "What country issued your passport?": "Ki peyi ki te bay paspo ou?",
      "What was your place of entry into the USA? (Ex: El Paso, TX)": "Ki kote ou te antre Ozetazini? (Egz.: El Paso, TX)",
      "What was your Status when you last entered the United States?": "Ki estati ou te genyen le ou te antre Ozetazini denye fwa?",
      "What was your status when you last entered the United States?": "Ki estati ou te genyen le ou te antre Ozetazini denye fwa?",
      "Do you have any criminal history?": "Eske ou gen antecedan penal?",
      "Select YES here to continue.": "Chwazi WI isit la pou kontinye.",
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
    const cleanText = text.trim();
    const prompts = promptTranslations[language] || {};
    if (prompts[cleanText]) {
      return prompts[cleanText];
    }
    const normalizedText = normalizePrompt(cleanText);
    for (const [source, translation] of Object.entries(prompts)) {
      if (normalizePrompt(source) === normalizedText) {
        return translation;
      }
    }
    return patternPromptFor(language, cleanText) || textFor(language, cleanText);
  }

  function normalizePrompt(value) {
    return value
      .toLowerCase()
      .replace(/[’]/g, "'")
      .replace(/\s+/g, " ")
      .replace(/[?.:]+$/g, "")
      .trim();
  }

  function patternPromptFor(language, text) {
    const normalized = normalizePrompt(text);
    const whatMatch = normalized.match(/^what is your (.+)$/);
    if (whatMatch) {
      const subject = fieldLabelFor(language, whatMatch[1]);
      if (language === "es") {
        return `Cual es su ${subject}?`;
      }
      if (language === "ht") {
        return `Ki ${subject} ou?`;
      }
    }
    const whatWasMatch = normalized.match(/^what was your (.+)$/);
    if (whatWasMatch) {
      const subject = fieldLabelFor(language, whatWasMatch[1]);
      if (language === "es") {
        return `Cual fue su ${subject}?`;
      }
      if (language === "ht") {
        return `Ki ${subject} ou te genyen?`;
      }
    }
    const doYouHaveMatch = normalized.match(/^do you have (.+)$/);
    if (doYouHaveMatch) {
      const subject = fieldLabelFor(language, doYouHaveMatch[1]);
      if (language === "es") {
        return `Tiene ${subject}?`;
      }
      if (language === "ht") {
        return `Eske ou gen ${subject}?`;
      }
    }
    const haveYouMatch = normalized.match(/^have you (.+)$/);
    if (haveYouMatch) {
      const subject = fieldLabelFor(language, haveYouMatch[1]);
      if (language === "es") {
        return `Ha ${subject}?`;
      }
      if (language === "ht") {
        return `Eske ou te ${subject}?`;
      }
    }
    const whenDidMatch = normalized.match(/^when did you (.+)$/);
    if (whenDidMatch) {
      const subject = fieldLabelFor(language, whenDidMatch[1]);
      if (language === "es") {
        return `Cuando ${subject}?`;
      }
      if (language === "ht") {
        return `Ki le ou te ${subject}?`;
      }
    }
    if (normalized.startsWith("select yes")) {
      if (language === "es") {
        return "Seleccione SI aqui para continuar.";
      }
      if (language === "ht") {
        return "Chwazi WI isit la pou kontinye.";
      }
    }
    return null;
  }

  function fieldLabelFor(language, subject) {
    const labels = {
      es: {
        "first name": "primer nombre",
        "middle name": "segundo nombre",
        "last name": "apellido",
        "last name(s)": "apellido(s)",
        "current address": "direccion actual",
        "mailing address": "direccion postal",
        "phone number": "numero de telefono",
        "email address": "correo electronico",
        "date of birth": "fecha de nacimiento",
        "country of birth": "pais de nacimiento",
        "country of citizenship": "pais de ciudadania",
        "passport number": "numero de pasaporte",
        "passport expiration date": "fecha de vencimiento del pasaporte",
        "passport country of issuance": "pais de emision del pasaporte",
        "country issued your passport": "pais que emitio su pasaporte",
        "i-94 number": "numero I-94",
        "i94 number": "numero I-94",
        "uscis online account number": "numero de cuenta en linea de USCIS",
        "place of entry into the usa": "lugar de entrada a los Estados Unidos",
        "place of entry into the usa (ex: el paso, tx)": "lugar de entrada a los Estados Unidos (Ej.: El Paso, TX)",
        "status when you last entered the united states": "estatus cuando entro por ultima vez a los Estados Unidos",
        "last enter the united states": "entro por ultima vez a los Estados Unidos",
        "filed for work authorization before": "solicitado autorizacion de empleo anteriormente",
        "any criminal history": "algun antecedente penal",
        "a-number": "A-Number",
        "social security number": "numero de Seguro Social"
      },
      ht: {
        "first name": "prenon",
        "middle name": "dezyem non",
        "last name": "siyati",
        "last name(s)": "siyati",
        "current address": "adres aktyel",
        "mailing address": "adres postal",
        "phone number": "nimewo telefon",
        "email address": "imel",
        "date of birth": "dat nesans",
        "country of birth": "peyi kote ou fet",
        "country of citizenship": "peyi sitwayente",
        "passport number": "nimewo paspo",
        "passport expiration date": "dat ekspirasyon paspo",
        "passport country of issuance": "peyi ki bay paspo a",
        "country issued your passport": "peyi ki te bay paspo ou",
        "i-94 number": "nimewo I-94",
        "i94 number": "nimewo I-94",
        "uscis online account number": "nimewo kont sou entenet USCIS",
        "place of entry into the usa": "kote ou te antre Ozetazini",
        "place of entry into the usa (ex: el paso, tx)": "kote ou te antre Ozetazini (Egz.: El Paso, TX)",
        "status when you last entered the united states": "estati ou le ou te antre Ozetazini denye fwa",
        "last enter the united states": "antre Ozetazini denye fwa",
        "filed for work authorization before": "deja aplike pou otorizasyon travay",
        "any criminal history": "antecedan penal",
        "a-number": "A-Number",
        "social security number": "nimewo Sekirite Sosyal"
      }
    };
    return labels[language]?.[subject] || subject;
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
