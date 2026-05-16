(function () {
  const STORAGE_KEY = "apexClientLanguage";
  const dictionaries = {
    es: {
      language_label: "Idioma",
      agency: "Agencia",
      client_portal: "Portal del cliente",
      client_portal_help: "Revise las actualizaciones de su caso, la informacion de contacto de su agencia y las acciones solicitadas.",
      client_information: "Su informacion",
      agency_information: "Informacion de la agencia",
      address: "Direccion",
      phone: "Telefono",
      email: "Correo electronico",
      a_number: "Numero A",
      open: "Abrir",
      your_cases: "Sus casos",
      current_status: "Estado actual",
      your_case_status: "Estado de su caso",
      no_crm_cases_assigned: "Aun no hay casos de CRM asignados.",
      back_to_dashboard: "Volver al panel",
      case_type: "Tipo de caso",
      case_timeline: "Linea de tiempo del caso",
      timeline_help: "Su agencia actualiza esta linea de tiempo cuando cambia el estado del caso.",
      upload_case_documents: "Subir documentos del caso",
      case_questionnaire: "Cuestionarios del caso",
      case_questionnaire_help: "Su agencia necesita que complete este cuestionario para este caso.",
      case_appointments: "Citas del caso",
      case_appointments_help: "Estas citas estan relacionadas con este caso.",
      no_case_appointments: "Aun no hay citas programadas para este caso.",
      duration: "Duracion",
      minutes: "minutos",
      client_upload_help: "Suba los documentos solicitados por su agencia. Puede volver mas tarde y agregar mas.",
      no_case_questionnaire: "Aun no se ha solicitado ningun cuestionario para este caso.",
      choose_document: "Elegir documento",
      choose_file: "Elegir archivo",
      no_file_chosen: "No se ha elegido archivo",
      optional_note: "Nota opcional",
      document_note_placeholder: "Ejemplo: copia del pasaporte, acta de nacimiento, recibo",
      upload_security_note: "Por seguridad, solo se aceptan archivos PDF, imagen y DOCX.",
      upload_document: "Subir documento",
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
      "Completed": "Completado",
      "Open": "Abierto",
      "Documents Received": "Documentos recibidos",
      "Documents Needed": "Documentos necesarios",
      "Documents Ready": "Documentos listos",
      "Scheduled": "Programada",
      "Canceled": "Cancelada",
      "No Show": "No se presento",
      "Re-scheduled": "Reprogramada"
    },
    ht: {
      language_label: "Lang",
      agency: "Ajans",
      client_portal: "Portal kliyan",
      client_portal_help: "Gade mizajou dosye ou, enfomasyon kontak ajans lan, ak aksyon yo mande yo.",
      client_information: "Enfomasyon ou",
      agency_information: "Enfomasyon ajans lan",
      address: "Adres",
      phone: "Telefon",
      email: "Imel",
      a_number: "Nimewo A",
      open: "Louvri",
      your_cases: "Dosye ou yo",
      current_status: "Estati aktyel",
      your_case_status: "Estati dosye ou",
      no_crm_cases_assigned: "Pa gen dosye CRM ki asiyen pou ou anko.",
      back_to_dashboard: "Retounen sou paj prensipal la",
      case_type: "Kalite dosye",
      case_timeline: "Liy tan dosye a",
      timeline_help: "Ajans ou mete liy tan sa a ajou le estati dosye a chanje.",
      upload_case_documents: "Telechaje dokiman dosye a",
      case_questionnaire: "Kesyone dosye a yo",
      case_questionnaire_help: "Ajans ou bezwen ou ranpli kesyone sa a pou dosye sa a.",
      case_appointments: "Randevou dosye a",
      case_appointments_help: "Randevou sa yo konekte ak dosye sa a.",
      no_case_appointments: "Pa gen randevou ki pwograme pou dosye sa a anko.",
      duration: "Dire",
      minutes: "minit",
      client_upload_help: "Telechaje dokiman ajans ou mande yo. Ou ka retounen pita pou ajoute plis.",
      no_case_questionnaire: "Pa gen kesyone ki mande pou dosye sa a anko.",
      choose_document: "Chwazi dokiman",
      choose_file: "Chwazi fichye",
      no_file_chosen: "Pa gen fichye chwazi",
      optional_note: "Not opsyonel",
      document_note_placeholder: "Egzanp: kopi paspo, batiste, resi",
      upload_security_note: "Pou sekirite, se selman PDF, imaj ak DOCX yo aksepte.",
      upload_document: "Telechaje dokiman",
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
      "Completed": "Fini",
      "Open": "Louvri",
      "Documents Received": "Dokiman resevwa",
      "Documents Needed": "Dokiman nesese",
      "Documents Ready": "Dokiman pare",
      "Scheduled": "Pwograme",
      "Canceled": "Anile",
      "No Show": "Pa vini",
      "Re-scheduled": "Repwograme"
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
      "Are you trying to renew you work authorization?": "Esta tratando de renovar su autorizacion de empleo?",
      "What is your address? (do not include city, state or zip code)?": "Cual es su direccion? (no incluya ciudad, estado ni codigo postal)",
      "What is your address? (Do not include city, state or zip code)": "Cual es su direccion? (no incluya ciudad, estado ni codigo postal)",
      "What is your Zip Code": "Cual es su codigo postal?",
      "What is your apartment/suite/floor number? (or n/a)?": "Cual es su apartamento, suite o piso? (o N/A)",
      "What is your current city?": "Cual es su ciudad actual?",
      "What is your state? (ex: fl)?": "Cual es su estado? (ej.: FL)",
      "What is your alien number? (or n/a)?": "Cual es su numero de extranjero? (o N/A)",
      "What is your uscis account number? (or n/a)?": "Cual es su numero de cuenta de USCIS? (o N/A)",
      "Do you have a USCIS Account Number? (or N/A)": "Tiene un numero de cuenta de USCIS? (o N/A)",
      "Have you filed for work permit before? (even if denied)": "Ha solicitado permiso de trabajo anteriormente? (incluso si fue negado)",
      "When did you Last Entry the USA? (mm/dd/yyyy)": "Cuando fue su ultima entrada a los Estados Unidos? (mm/dd/aaaa)",
      "What was your Status at your Last Entry? (or N/A)": "Cual fue su estatus en su ultima entrada? (o N/A)",
      "What is your Current Immigration Status? (or N/A)": "Cual es su estatus migratorio actual? (o N/A)",
      "What is your City or Municipality Of Birth?": "Cual es su ciudad o municipio de nacimiento?",
      "What is your State or Province of Birth?": "Cual es su estado o provincia de nacimiento?",
      "What is your Mobile Number?": "Cual es su numero de celular?",
      "What is your Email?": "Cual es su correo electronico?",
      "What Country Issued your passport? (or N/A)": "Que pais emitio su pasaporte? (o N/A)",
      "What is your date of birth? (mm/dd/yyyy)?": "Cual es su fecha de nacimiento? (mm/dd/aaaa)",
      "What is your social security number? (or n/a)?": "Cual es su numero de Seguro Social? (o N/A)",
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
      "Do you have any criminal record?": "Tiene algun antecedente penal?",
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
      "Are you trying to renew you work authorization?": "Eske w ap eseye renouvle otorizasyon travay ou?",
      "What is your address? (do not include city, state or zip code)?": "Ki adres ou? (pa mete vil, eta, oswa kod postal)",
      "What is your address? (Do not include city, state or zip code)": "Ki adres ou? (pa mete vil, eta, oswa kod postal)",
      "What is your Zip Code": "Ki kod postal ou?",
      "What is your apartment/suite/floor number? (or n/a)?": "Ki apatman, suite, oswa etaj ou? (oswa N/A)",
      "What is your current city?": "Ki vil ou ye kounye a?",
      "What is your state? (ex: fl)?": "Ki eta ou? (egz.: FL)",
      "What is your alien number? (or n/a)?": "Ki nimewo etranje ou? (oswa N/A)",
      "What is your uscis account number? (or n/a)?": "Ki nimewo kont USCIS ou? (oswa N/A)",
      "Do you have a USCIS Account Number? (or N/A)": "Eske ou gen yon nimewo kont USCIS? (oswa N/A)",
      "Have you filed for work permit before? (even if denied)": "Eske ou te deja aplike pou pemisyon travay? (menm si yo te refize li)",
      "When did you Last Entry the USA? (mm/dd/yyyy)": "Ki le ou te antre Ozetazini denye fwa? (mm/dd/aaaa)",
      "What was your Status at your Last Entry? (or N/A)": "Ki estati ou te genyen nan denye antre ou? (oswa N/A)",
      "What is your Current Immigration Status? (or N/A)": "Ki estati imigrasyon ou kounye a? (oswa N/A)",
      "What is your City or Municipality Of Birth?": "Ki vil oswa minisipalite kote ou fet?",
      "What is your State or Province of Birth?": "Ki eta oswa pwovens kote ou fet?",
      "What is your Mobile Number?": "Ki nimewo selile ou?",
      "What is your Email?": "Ki imel ou?",
      "What Country Issued your passport? (or N/A)": "Ki peyi ki te bay paspo ou? (oswa N/A)",
      "What is your date of birth? (mm/dd/yyyy)?": "Ki dat nesans ou? (mm/dd/aaaa)",
      "What is your social security number? (or n/a)?": "Ki nimewo Sekirite Sosyal ou? (oswa N/A)",
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
      "Do you have any criminal record?": "Eske ou gen antecedan penal?",
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
    const compactText = normalizePrompt(cleanText).replace(/[^a-z0-9]/g, "");
    if (
      compactText.includes("firsttime") &&
      compactText.includes("category")
    ) {
      if (language === "es") {
        return "Es la primera vez que solicita autorizacion de empleo bajo esta categoria?";
      }
      if (language === "ht") {
        return "Eske se premye fwa ou aplike pou otorizasyon travay anba kategori sa a?";
      }
    }
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
    if (
      normalized.includes("first time") &&
      normalized.includes("category")
    ) {
      if (language === "es") {
        return "Es la primera vez que solicita autorizacion de empleo bajo esta categoria?";
      }
      if (language === "ht") {
        return "Eske se premye fwa ou aplike pou otorizasyon travay anba kategori sa a?";
      }
    }
    if (normalized.startsWith("what is your address")) {
      if (language === "es") {
        return "Cual es su direccion? (no incluya ciudad, estado ni codigo postal)";
      }
      if (language === "ht") {
        return "Ki adres ou? (pa mete vil, eta, oswa kod postal)";
      }
    }
    const whatMatch = normalized.match(/^what is your (.+)$/);
    if (whatMatch) {
      const subject = fieldLabelFor(language, whatMatch[1]);
      if (!subject) {
        return null;
      }
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
      if (!subject) {
        return null;
      }
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
      if (!subject) {
        return null;
      }
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
      if (!subject) {
        return null;
      }
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
      if (!subject) {
        return null;
      }
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
        "address": "direccion",
        "address (do not include city, state or zip code)": "direccion (no incluya ciudad, estado ni codigo postal)",
        "apartment/suite/floor number": "apartamento, suite o piso",
        "current city": "ciudad actual",
        "state": "estado",
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
        "uscis account number": "numero de cuenta de USCIS",
        "alien number": "numero de extranjero",
        "place of entry into the usa": "lugar de entrada a los Estados Unidos",
        "place of entry into the usa (ex: el paso, tx)": "lugar de entrada a los Estados Unidos (Ej.: El Paso, TX)",
        "status when you last entered the united states": "estatus cuando entro por ultima vez a los Estados Unidos",
        "last enter the united states": "entro por ultima vez a los Estados Unidos",
        "filed for work authorization before": "solicitado autorizacion de empleo anteriormente",
        "any criminal history": "algun antecedente penal",
        "any criminal record": "algun antecedente penal",
        "a-number": "A-Number",
        "social security number": "numero de Seguro Social"
      },
      ht: {
        "first name": "prenon",
        "middle name": "dezyem non",
        "last name": "siyati",
        "last name(s)": "siyati",
        "address": "adres",
        "address (do not include city, state or zip code)": "adres (pa mete vil, eta, oswa kod postal)",
        "apartment/suite/floor number": "apatman, suite, oswa etaj",
        "current city": "vil ou ye kounye a",
        "state": "eta",
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
        "uscis account number": "nimewo kont USCIS",
        "alien number": "nimewo etranje",
        "place of entry into the usa": "kote ou te antre Ozetazini",
        "place of entry into the usa (ex: el paso, tx)": "kote ou te antre Ozetazini (Egz.: El Paso, TX)",
        "status when you last entered the united states": "estati ou le ou te antre Ozetazini denye fwa",
        "last enter the united states": "antre Ozetazini denye fwa",
        "filed for work authorization before": "deja aplike pou otorizasyon travay",
        "any criminal history": "antecedan penal",
        "any criminal record": "antecedan penal",
        "a-number": "A-Number",
        "social security number": "nimewo Sekirite Sosyal"
      }
    };
    const cleanSubject = subject
      .replace(/\?/g, "")
      .replace(/\s*\(or n\/a\)\s*$/i, "")
      .replace(/\s*\(or n\/a\s*$/i, "")
      .replace(/\s*\(mm\/dd\/yyyy\)\s*$/i, "")
      .replace(/\s+/g, " ")
      .trim();
    return labels[language]?.[cleanSubject] || null;
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
      const translatedText =
        promptFor(language, element.dataset.i18nText || "") ||
        promptFor(language, element.dataset.originalText || "") ||
        promptFor(language, element.textContent || "");
      element.textContent = translatedText || element.dataset.originalText;
    });
    document.querySelectorAll("[data-i18n-placeholder]").forEach((element) => {
      if (!element.dataset.originalPlaceholder) {
        element.dataset.originalPlaceholder = element.getAttribute("placeholder") || "";
      }
      element.setAttribute(
        "placeholder",
        textFor(language, element.dataset.i18nPlaceholder) || element.dataset.originalPlaceholder
      );
    });
    document.querySelectorAll("[data-file-name]").forEach((element) => {
      const fileInput = document.getElementById("client-document-input");
      if (!fileInput?.files?.length) {
        element.textContent = textFor(language, "no_file_chosen") || "No file chosen";
      }
    });
    document.querySelectorAll(".question-prompt, .question-map-link b").forEach((element) => {
      if (!element.dataset.originalText) {
        element.dataset.originalText = element.textContent;
      }
      const translatedText =
        promptFor(language, element.dataset.i18nText || "") ||
        promptFor(language, element.dataset.originalText || "") ||
        promptFor(language, element.textContent || "");
      if (translatedText) {
        element.textContent = translatedText;
      }
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
  const fileInput = document.getElementById("client-document-input");
  const fileName = document.querySelector("[data-file-name]");
  fileInput?.addEventListener("change", () => {
    const language = localStorage.getItem(STORAGE_KEY) || "en";
    if (fileName) {
      fileName.textContent = fileInput.files?.[0]?.name || textFor(language, "no_file_chosen") || "No file chosen";
    }
  });
  applyLanguage(savedLanguage);
})();
