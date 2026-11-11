/**
 * College Placement Alert Agent: Google Apps Script version (free, near-instant).
 *
 * Runs inside your own Google account, checks Gmail every minute, and sends a Telegram
 * alert for new placement emails from your official placement-cell senders.
 * Read-only on Gmail: it never replies, deletes, labels or applies to anything.
 *
 * Setup (once):
 *   1. script.google.com → New project → paste this whole file into Code.gs → Save.
 *   2. Project Settings (gear) → Script properties → add:
 *        TELEGRAM_BOT_TOKEN = your bot token
 *        TELEGRAM_CHAT_ID   = your chat id
 *   3. Select the function `setup` at the top and click Run. Allow the permissions.
 *      This sends a Telegram test message, marks existing emails as seen, and starts the
 *      every-minute check.
 *   Optional: run `resendRecent` to re-send alerts for the last 3 days (sample check).
 */

// ---------------- Configuration ----------------
var SENDERS = [
  'kabita_tnp@vnrvjiet.in',
  'poonam_tnp@vnrvjiet.in',
  'training@vnrvjiet.in',
  'notifications@pod.ai',
];
// Require Gmail's DKIM/DMARC check to pass for the sender's domain (blocks spoofed mail).
// Senders listed here skip that check (e.g. platforms that send through another mail service).
var REQUIRE_AUTHENTICATION = false; // some placement-cell mails fail DKIM/DMARC, so this is off
var SKIP_AUTH_FOR = [];
// Emails from these senders are forwarded to Telegram exactly as they are (full text plus every
// attachment such as PDFs and images), with no placement filtering.
var FORWARD_AS_IS = [
  'vedeshridhvi@gmail.com',
];
var LOOKBACK_MINUTES = 120; // each run looks at mail from the last 2 hours
var NOT_MENTIONED = 'Not mentioned in email';

// ---------------- Entry points ----------------

function setup() {
  sendTelegram_('College Placement Alert: test message. If you can read this, Gmail alerts are set up.');
  // Mark existing mail as seen so you don't get old alerts.
  var ids = recentMessages_(3 * 24 * 60).map(function (m) { return m.getId(); });
  saveSeen_(ids);
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'checkMail') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('checkMail').timeBased().everyMinutes(1).create();
  Logger.log('Setup done: ' + ids.length + ' existing email(s) marked as seen; checking every minute.');
}

function checkMail() {
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(5000)) return;
  try {
    var seen = loadSeen_();
    var seenSet = {};
    seen.forEach(function (id) { seenSet[id] = true; });
    var messages = recentMessages_(LOOKBACK_MINUTES);
    var newIds = [];
    messages.forEach(function (m) {
      var id = m.getId();
      if (seenSet[id]) return;
      try {
        processMessage_(m);
        newIds.push(id);
      } catch (e) {
        // Not marked as seen, so it is retried next minute.
        Logger.log('Failed on a message: ' + e);
      }
    });
    if (newIds.length) saveSeen_(seen.concat(newIds));
  } finally {
    lock.releaseLock();
  }
}

function resendRecent() {
  var msgs = recentMessages_(3 * 24 * 60).reverse(); // oldest first
  var counts = {};
  msgs.forEach(function (m) {
    var status = processMessage_(m);
    counts[status] = (counts[status] || 0) + 1;
  });
  Logger.log('Resend: ' + JSON.stringify(counts));
}

function sendTestMessage() {
  sendTelegram_('College Placement Alert: test message.');
}

// ---------------- Gmail ----------------

function recentMessages_(minutes) {
  var after = Math.floor((Date.now() - minutes * 60 * 1000) / 1000);
  var query = 'from:(' + SENDERS.concat(FORWARD_AS_IS).join(' OR ') + ') after:' + after;
  var cutoff = new Date(after * 1000);
  var out = [];
  GmailApp.search(query, 0, 50).forEach(function (thread) {
    thread.getMessages().forEach(function (m) {
      var addr = extractAddress_(m.getFrom());
      if (m.getDate() >= cutoff && (isAllowlisted_(addr) || isForwardAsIs_(addr))) out.push(m);
    });
  });
  out.sort(function (a, b) { return b.getDate() - a.getDate(); });
  return out;
}

function processMessage_(m) {
  var address = extractAddress_(m.getFrom());
  if (isForwardAsIs_(address)) {
    forwardAsIs_(m);
    return 'forwarded';
  }
  if (!isAllowlisted_(address)) return 'skipped_sender';
  if (REQUIRE_AUTHENTICATION && SKIP_AUTH_FOR.indexOf(address) < 0 &&
      !isAuthenticated_(address.split('@')[1], [m.getHeader('Authentication-Results') || ''])) {
    return 'skipped_sender';
  }
  var email = { id: m.getId(), subject: m.getSubject() || '', body: m.getPlainBody() || '' };
  var c = classifyEmail(email.subject, email.body);
  if (!c.is_placement_related) return 'not_placement';
  sendTelegram_(renderText(c, email.subject, 'https://mail.google.com/mail/u/0/#all/' + email.id));
  return 'alerted';
}

// Sends the whole email to Telegram: header, full text (split into 4,000-character parts,
// Telegram's limit), then each attachment as a file. Telegram bots can send files up to 50 MB.
function forwardAsIs_(m) {
  var header = 'Email from ' + m.getFrom() + '\n' +
    'Subject: ' + (m.getSubject() || '(no subject)') + '\n' +
    'Date: ' + Utilities.formatDate(m.getDate(), Session.getScriptTimeZone(), 'd MMM yyyy, h:mm a') + '\n' +
    'Open in Gmail: https://mail.google.com/mail/u/0/#all/' + m.getId();
  var body = (m.getPlainBody() || '').replace(/\r/g, '').replace(/\n{3,}/g, '\n\n').trim();
  sendTelegramRaw_(header);
  for (var i = 0; i < body.length; i += 4000) sendTelegramRaw_(body.slice(i, i + 4000));
  m.getAttachments({ includeInlineImages: true }).forEach(function (a) {
    if (a.getSize() > 50 * 1024 * 1024) {
      sendTelegramRaw_('Attachment too large for Telegram (over 50 MB): ' + a.getName() + '. Open it in Gmail.');
    } else {
      sendTelegramDocument_(a.copyBlob().setName(a.getName() || 'attachment'));
    }
  });
}

function isForwardAsIs_(address) {
  return FORWARD_AS_IS.map(function (s) { return s.toLowerCase(); }).indexOf(address) >= 0;
}

// ---------------- Seen-message store ----------------

function loadSeen_() {
  var raw = PropertiesService.getScriptProperties().getProperty('SEEN_IDS');
  return raw ? JSON.parse(raw) : [];
}

function saveSeen_(ids) {
  PropertiesService.getScriptProperties().setProperty('SEEN_IDS', JSON.stringify(ids.slice(-400)));
}

// ---------------- Telegram ----------------

function sendTelegram_(text) {
  sendTelegramRaw_(text.replace(/\*/g, ''));
}

function sendTelegramRaw_(text) {
  telegramCall_('sendMessage', {
    contentType: 'application/json',
    payload: JSON.stringify({ chat_id: telegramConfig_().chat, text: text || '(empty)', disable_web_page_preview: true }),
  });
}

function sendTelegramDocument_(blob) {
  // A blob in the payload makes UrlFetchApp send multipart/form-data, as Telegram expects.
  telegramCall_('sendDocument', { payload: { chat_id: telegramConfig_().chat, document: blob } });
}

function telegramConfig_() {
  var props = PropertiesService.getScriptProperties();
  var token = props.getProperty('TELEGRAM_BOT_TOKEN');
  var chat = props.getProperty('TELEGRAM_CHAT_ID');
  if (!token || !chat) throw new Error('Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in Script properties');
  return { token: token, chat: chat };
}

function telegramCall_(method, options) {
  var token = telegramConfig_().token;
  for (var attempt = 0; attempt < 3; attempt++) {
    var params = { method: 'post', payload: options.payload, muteHttpExceptions: true };
    if (options.contentType) params.contentType = options.contentType;
    var r = UrlFetchApp.fetch('https://api.telegram.org/bot' + token + '/' + method, params);
    var code = r.getResponseCode();
    if (code < 300) return;
    if (code < 500 && code !== 429) throw new Error('Telegram error ' + code + ': ' + r.getContentText().slice(0, 200));
    Utilities.sleep(1000 * Math.pow(2, attempt));
  }
  throw new Error('Telegram unavailable');
}

// ---------------- Sender verification ----------------

function extractAddress_(from) {
  var m = /<([^>]+)>/.exec(from || '');
  return (m ? m[1] : from || '').trim().toLowerCase();
}

function isAllowlisted_(address) {
  return SENDERS.map(function (s) { return s.toLowerCase(); }).indexOf(address) >= 0;
}

function isAuthenticated_(domain, authResults) {
  domain = (domain || '').toLowerCase();
  for (var i = 0; i < authResults.length; i++) {
    var h = authResults[i].toLowerCase();
    var m, re = /dmarc=pass[^;]*?header\.from=([a-z0-9.\-]+)/g;
    while ((m = re.exec(h))) if (m[1] === domain) return true;
    re = /dkim=pass[^;]*?header\.(?:i|d)=@?([a-z0-9.\-]+)/g;
    while ((m = re.exec(h))) {
      if (domain === m[1] || domain.slice(-(m[1].length + 1)) === '.' + m[1]) return true;
    }
  }
  return false;
}

// ---------------- Classifier (same rules as app/classifier/rules.py) ----------------

var CATEGORY_PATTERNS = [
  ['RESULT', [/\bfinal(ly)? selected\b/, /\bselected (students|candidates)\b/, /\boffer letters?\b/,
    /\bresults? (of|for)\b/, /\bcongratulations\b/, /\bplaced students\b/]],
  ['SHORTLIST', [/\bshort[\s-]?listed\b/, /\bshort[\s-]?list\b/, /\bselected for (the )?next round\b/,
    /\bcleared (the )?(online )?(test|assessment|round)\b/]],
  ['INTERVIEW_SCHEDULE', [/\binterviews?\b/, /\bhr round\b/, /\btechnical round\b/, /\bgroup discussion\b/,
    /\b(gd|pi) round\b/]],
  ['ASSESSMENT', [/\bonline (test|assessment|exam)\b/, /\bassessments?\b/, /\bcoding (round|test|challenge)\b/,
    /\baptitude\b/, /\btest link\b/, /\bhackerrank\b/, /\bhackerearth\b/, /\bmettl\b/, /\bamcat\b/, /\bcocubes\b/]],
  ['PRE_PLACEMENT_TALK', [/\bpre[\s-]?placement talk\b/, /\bppt\b/, /\bcompany presentation\b/]],
  ['PLACEMENT_DRIVE', [/\b(campus|placement|recruitment|hiring|off[\s-]?campus|pool[\s-]?campus) drive\b/,
    /\bcampus (recruitment|placement|hiring)\b/, /\brecruitment\b/, /\bhiring\b/,
    /\bjob (opening|opportunity|role)\b/, /\binternship (opportunity|drive|offer)\b/, /\bctc\b/,
    /\bvisiting (our )?campus\b/, /\bplacement (update|notice|opportunity|process)\b/, /\bstipend\b/,
    /\bpackage\b.*\blpa\b/, /\blpa\b/, /\bjob description\b/]],
  ['REGISTRATION', [/\bregistrations?\b/, /\bregister\b/, /\bapply (now|before|by)\b/, /\blast date\b/,
    /\bdeadline\b/, /\bgoogle form\b/, /forms\.gle\//]],
  ['ELIGIBILITY', [/\beligib(le|ility)\b/, /\bcgpa\b/, /\bbacklogs?\b/]],
];
var IGNORE_PATTERNS = [/\bfest\b/, /\bfee(s)? (payment|due)\b/, /\bnewsletter\b/, /\bholiday\b/,
  /\bexam(ination)? (time ?table|schedule)\b/, /\bunsubscribe\b/];

var MONTHS = '(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*';
var DATE_RE = new RegExp('(\\b\\d{1,2}(st|nd|rd|th)?[\\s\\-]*' + MONTHS + '\\.?,?(\\s*\\d{4})?' +
  '|\\b' + MONTHS + '\\.?\\s+\\d{1,2}(st|nd|rd|th)?,?(\\s*\\d{4})?' +
  '|\\b\\d{1,2}[./-]\\d{1,2}[./-]\\d{2,4}\\b|\\b(today|tomorrow)\\b)', 'i');
var TIME_RE = /\b\d{1,2}([:.]\d{2})?\s*(am|pm|a\.m\.|p\.m\.|hrs)\b/i;
var LINK_RE = /https?:\/\/[^\s<>")\]]+/i;

var LABELS = {
  company: '(company|organi[sz]ation|company name|recruiter)',
  role: '(role|job role|designation|position|profile|job title)',
  eligibility: '(eligibility( criteria)?|eligible (branches|streams|courses)|criteria)',
  registration_deadline: '(last date( (to|for) (register|registration|apply))?|deadline|registration (closes|deadline|ends)|apply by)',
  event_date_time: '(date of (the )?(drive|test|exam|interview|assessment|ppt)|(drive|test|interview|assessment|exam) date|date ?(&|and) ?time|date|schedule)',
  venue_or_link: '(venue|location|mode|meeting link|test link|registration link|link)',
};

var STOPWORDS = ('a an the all dear students student candidates batch year final pre b.tech btech b.e m.tech mtech ' +
  'mca mba cse ece eee it mech civil campus placement placements registration registrations drive drives hiring ' +
  'recruitment internship internships interview interviews test online off pool training tnp t&p tpo vnr vjiet ' +
  'vnrvjiet ppt shortlist shortlisted result results selected important urgent reminder re fwd fw last date ' +
  'deadline job jobs opportunity opportunities role roles update updates notice info information details ' +
  'regarding reg new upcoming virtual on for of and & by with at from is are dt assessment talk college ' +
  'institute university program programme hackathon webinar session').split(' ');
var NAME_START_RE = /^([A-Z][\w&.'’-]*(?:[ \t]+(?:[A-Z0-9][\w&.'’-]*|&|of|and)){0,5})/;
var NAME_END_RE = /([A-Z][\w&.'’-]*(?:[ \t]+(?:[A-Z0-9][\w&.'’-]*|&|of|and)){0,5})[ \t]*$/;
var KW = '(campus|recruitment|hiring|drive|placement|off[- ]?campus|pool[- ]?campus|internship|registration' +
  '|ppt|pre[- ]?placement|shortlist(ed)?|interviews?|online test|assessment|results?|job)';

function labelled_(text, label) {
  var re = new RegExp('^\\s*[*•\\-]?\\s*' + label + '\\s*[:\\-–]\\s*(.+)$', 'im');
  var m = re.exec(text);
  if (!m) return null;
  var v = m[m.length - 1].replace(/^[ *\t]+|[ *\t]+$/g, '');
  return v ? v.slice(0, 300) : null;
}

function cleanCompany_(raw) {
  if (!raw) return null;
  var words = raw.split(/\s+/).filter(Boolean);
  var stop = function (w) { return STOPWORDS.indexOf(w.toLowerCase().replace(/^[.,:]+|[.,:]+$/g, '')) >= 0; };
  while (words.length && stop(words[0])) words.shift();
  while (words.length && (stop(words[words.length - 1]) || /^\d{4}$/.test(words[words.length - 1]))) words.pop();
  var name = words.join(' ').replace(/^[ .,:\-–|]+|[ .,:\-–|]+$/g, '');
  if (name.length < 2 || /vnr|vjiet|vignana/i.test(name)) return null;
  return name;
}

function guessCompany_(subject, body) {
  subject = (subject || '').replace(/^\s*((re|fwd?|fw)\s*:\s*)+/i, '');
  var texts = [subject, (body || '').slice(0, 2000)];
  for (var t = 0; t < texts.length; t++) {
    var lines = texts[t].split('\n');
    for (var l = 0; l < lines.length; l++) {
      var line = lines[l], m, re, name;
      var before = [
        /[ \t]+(is|are|will be)[ \t]+(visiting|hiring|recruiting|conducting|coming|organi[sz]ing|looking|inviting)/gi,
        new RegExp('[ \\t]*([-–:|][ \\t]*)?' + KW, 'gi'),
      ];
      for (var p = 0; p < before.length; p++) {
        re = before[p];
        while ((m = re.exec(line))) {
          var nm = NAME_END_RE.exec(line.slice(0, m.index));
          name = cleanCompany_(nm && nm[1]);
          if (name) return name;
          if (m[0].length === 0) re.lastIndex++;
        }
      }
      re = new RegExp(KW + '[ \\t]*(students|candidates|list|schedule|link|process|drive|talk)?[ \\t]*' +
        '(by|for|of|from|with|at|[-–:|])[ \\t]*', 'gi');
      while ((m = re.exec(line))) {
        var ns = NAME_START_RE.exec(line.slice(m.index + m[0].length));
        name = cleanCompany_(ns && ns[1]);
        if (name) return name;
      }
    }
  }
  return null;
}

function deadlineSentence_(text) {
  var parts = text.split(/(?<=[.!?])\s+|\n/);
  for (var i = 0; i < parts.length; i++) {
    var s = parts[i].trim();
    if (/\b(before|by|last date|deadline|on or before|closes?|till|until)\b/i.test(s) && /\b(regist|apply|submit|fill)/i.test(s)) {
      return DATE_RE.test(s) ? s.slice(0, 300) : 'Unclear: ' + s.slice(0, 280);
    }
  }
  return null;
}

function excerpt_(body, limit) {
  limit = limit || 350;
  var text = body.replace(/\s+/g, ' ').replace(/^(dear|hello|hi)\s+(all|students?|candidates?)[,!]?\s*/i, '');
  return text.slice(0, limit).replace(/\s+$/, '') + (text.length > limit ? '…' : '');
}

var PRIORITY = { PLACEMENT_DRIVE: 'URGENT', REGISTRATION: 'URGENT', SHORTLIST: 'URGENT', INTERVIEW_SCHEDULE: 'URGENT',
  RESULT: 'URGENT', ASSESSMENT: 'HIGH', PRE_PLACEMENT_TALK: 'HIGH', ELIGIBILITY: 'HIGH' };
var CATEGORY_LABEL = { PLACEMENT_DRIVE: 'New placement drive', REGISTRATION: 'Registration', ELIGIBILITY: 'Eligibility criteria',
  SHORTLIST: 'Shortlist', ASSESSMENT: 'Online test / assessment', INTERVIEW_SCHEDULE: 'Interview schedule',
  PRE_PLACEMENT_TALK: 'Pre-placement talk', RESULT: 'Result / offer update' };
var ACTIONS = {
  PLACEMENT_DRIVE: 'Read the drive details and register if eligible.',
  REGISTRATION: 'Complete the registration before the deadline.',
  ELIGIBILITY: 'Check whether you meet the eligibility criteria.',
  SHORTLIST: 'Check whether your name is on the shortlist.',
  ASSESSMENT: 'Prepare for and attend the assessment on time.',
  INTERVIEW_SCHEDULE: 'Check your interview slot and be ready on time.',
  PRE_PLACEMENT_TALK: 'Attend the pre-placement talk.',
  RESULT: 'Check the result and any next steps in the email.',
};

function classifyEmail(subject, body) {
  var lower = (subject + '\n' + body).toLowerCase();
  // The subject decides the category when it can (a drive email that mentions its online test
  // is still a drive); otherwise the whole email is used.
  var subjectLower = subject.toLowerCase();
  var category = 'NOT_PLACEMENT';
  CATEGORY_PATTERNS.forEach(function (entry) {
    if (category === 'NOT_PLACEMENT' && entry[1].some(function (re) { return re.test(subjectLower); })) category = entry[0];
  });
  CATEGORY_PATTERNS.forEach(function (entry) {
    var hit = entry[1].some(function (re) { return re.test(lower); });
    if (hit && category === 'NOT_PLACEMENT') category = entry[0];
  });
  var ignored = IGNORE_PATTERNS.some(function (re) { return re.test(lower); });
  var strong = ['NOT_PLACEMENT', 'REGISTRATION', 'ELIGIBILITY'].indexOf(category) < 0;
  if (ignored && !strong) category = 'NOT_PLACEMENT';
  var isPlacement = category !== 'NOT_PLACEMENT';

  var f = {};
  Object.keys(LABELS).forEach(function (k) { f[k] = labelled_(body, LABELS[k]); });
  if (!f.company && isPlacement) f.company = guessCompany_(subject, body);
  if (!f.registration_deadline) f.registration_deadline = deadlineSentence_(body);
  if (!f.venue_or_link) { var link = LINK_RE.exec(body); f.venue_or_link = link ? link[0] : null; }
  if (!f.event_date_time && ['ASSESSMENT', 'INTERVIEW_SCHEDULE', 'PRE_PLACEMENT_TALK'].indexOf(category) >= 0) {
    var d = DATE_RE.exec(body);
    if (d) {
      var rest = body.slice(d.index + d[0].length);
      var tm = TIME_RE.exec(rest);
      f.event_date_time = d[0] + (tm && tm.index < 40 ? ', ' + tm[0] : '');
    }
  }
  return {
    is_placement_related: isPlacement,
    category: category,
    priority: PRIORITY[category] || 'HIGH',
    category_label: CATEGORY_LABEL[category] || 'Placement update',
    company: f.company || NOT_MENTIONED,
    role: f.role || NOT_MENTIONED,
    eligibility: f.eligibility || NOT_MENTIONED,
    registration_deadline: f.registration_deadline || NOT_MENTIONED,
    event_date_time: f.event_date_time || NOT_MENTIONED,
    venue_or_link: f.venue_or_link || NOT_MENTIONED,
    action_required: ACTIONS[category] || NOT_MENTIONED,
    summary: excerpt_(body),
  };
}

function clean_(v) {
  v = (v || '').replace(/[\r\n\t]+/g, ' ').trim().replace(/ {2,}/g, ' ');
  return v.length > 300 ? v.slice(0, 299) + '…' : v;
}

function renderText(c, subject, link) {
  var lines = ['College Placement Alert', '', c.priority + ' · ' + c.category_label.toUpperCase()];
  var add = function (label, value) {
    value = clean_(value);
    if (value && value !== NOT_MENTIONED) lines.push(label + ': ' + value);
  };
  add('Company', c.company);
  add('Role', c.role);
  add('Eligibility', c.eligibility);
  add('Registration deadline', c.registration_deadline);
  add('Date / time', c.event_date_time);
  add('Venue / link', c.venue_or_link);
  add('Next action', c.action_required);
  lines.push('');
  add('Summary', c.summary);
  lines.push('', 'Subject: ' + clean_(subject), 'Original email: ' + link);
  return lines.join('\n');
}

// Lets the classifier be tested with Node (ignored by Apps Script).
if (typeof module !== 'undefined') {
  module.exports = { classifyEmail: classifyEmail, renderText: renderText, isAuthenticated_: isAuthenticated_,
    extractAddress_: extractAddress_ };
}
