/**
 * ==========================================================================
 *  PROFESSIONAL TELEGRAM GROUP MANAGEMENT BOT
 *  Stack: Node.js + Telegraf + Supabase + wttr.in + OpenRouter AI
 *  Everything lives in this one file, as requested.
 * ==========================================================================
 *
 *  Setup:
 *   1. npm install
 *   2. cp .env.example .env   and fill in the values
 *   3. Run schema.sql in your Supabase project (SQL editor)
 *   4. node bot.js
 *
 *  Give the bot these admin rights in your group:
 *   - Ban/restrict users, Pin messages, Add new admins (to promote/demote)
 * ==========================================================================
 */

require('dotenv').config();
const { Telegraf, Markup } = require('telegraf');
const { createClient } = require('@supabase/supabase-js');

// -------------------------------------------------------------------------
// CONFIG
// -------------------------------------------------------------------------
const BOT_TOKEN = process.env.BOT_TOKEN;
const SUPABASE_URL = process.env.SUPABASE_URL;
const SUPABASE_SERVICE_KEY = process.env.SUPABASE_SERVICE_KEY;
const OPENROUTER_API_KEY = process.env.OPENROUTER_API_KEY;
const OPENROUTER_MODEL = process.env.OPENROUTER_MODEL || 'openai/gpt-4o-mini';

const ANONYMOUS_NAME = 'KnowledgePro Security System';

const SPAM_MSG_LIMIT = parseInt(process.env.SPAM_MSG_LIMIT || '6', 10);
const SPAM_WINDOW_MS = parseInt(process.env.SPAM_WINDOW_MS || '8000', 10);
const SPAM_MUTE_MINUTES = parseInt(process.env.SPAM_MUTE_MINUTES || '10', 10);

// Rose-bot style link filter
const LINK_REGEX = /(https?:\/\/|www\.|t\.me\/|telegram\.me\/)\S+/i;
const LINK_MUTE_MINUTES = parseInt(process.env.LINK_MUTE_MINUTES || '30', 10);

// Warn escalation (3 warns -> kick, per group policy)
const BLOCKWORD_WARN_LIMIT = parseInt(process.env.BLOCKWORD_WARN_LIMIT || '3', 10);

if (!BOT_TOKEN) {
  console.error('❌ BOT_TOKEN missing in .env');
  process.exit(1);
}
if (!SUPABASE_URL || !SUPABASE_SERVICE_KEY) {
  console.error('❌ SUPABASE_URL / SUPABASE_SERVICE_KEY missing in .env');
  process.exit(1);
}

const bot = new Telegraf(BOT_TOKEN);
const supabase = createClient(SUPABASE_URL, SUPABASE_SERVICE_KEY);

// -------------------------------------------------------------------------
// IN-MEMORY STATE (transient — resets on restart, by design)
// -------------------------------------------------------------------------
const afkMap = new Map();      // key: `${chatId}:${userId}` -> { reason, since, name }
const spamMap = new Map();     // key: `${chatId}:${userId}` -> [timestamps]
const pendingJoins = new Map(); // key: `${chatId}:${userId}` -> { chatId, userId, chatTitle }
const approvedUsers = new Set(); // key: `${chatId}:${userId}` -> exempt from link/spam/blockword filters (Rose-style /approve)

// Short-lived caches to avoid hammering the Telegram API / Supabase on every single message
const adminCache = new Map();      // key: `${chatId}:${userId}` -> { value, expires }
const blockwordsCache = new Map(); // key: chatId -> { value, expires }
const autoreplyCache = new Map();  // key: chatId -> { value, expires }
const ADMIN_CACHE_MS = 5 * 60 * 1000;   // admin status rarely changes — 5 min is safe
const CONFIG_CACHE_MS = 30 * 1000;      // blockwords/autoreplies — 30s, invalidated instantly on edit

function cacheGet(map, key) {
  const hit = map.get(key);
  if (hit && hit.expires > Date.now()) return hit.value;
  map.delete(key);
  return undefined;
}
function cacheSet(map, key, value, ttlMs) {
  map.set(key, { value, expires: Date.now() + ttlMs });
}

// -------------------------------------------------------------------------
// SMALL HELPERS
// -------------------------------------------------------------------------
function escapeHtml(str = '') {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function displayName(user) {
  if (!user) return 'Unknown';
  const name = [user.first_name, user.last_name].filter(Boolean).join(' ');
  return user.username ? `${name} (@${user.username})` : name;
}

function mention(user) {
  const name = escapeHtml(displayName(user));
  return `<a href="tg://user?id=${user.id}">${name}</a>`;
}

const BRAND_FOOTER = 'Knowledge Pro by AashirwadGamerzz';

/** Builds a Discord-embed-style formatted message using HTML parse mode.
 *  Every embed footer always carries the brand line. */
function embed({ emoji = '📌', title, lines = [], footer }) {
  let text = `${emoji} <b>${escapeHtml(title)}</b>\n`;
  text += '━━━━━━━━━━━━━━━\n';
  for (const line of lines) {
    text += `${line}\n`;
  }
  text += '━━━━━━━━━━━━━━━\n';
  if (footer) text += `<i>${footer}</i>\n`;
  text += `<i>${BRAND_FOOTER}</i>`;
  return text;
}

function fmtTime(ts) {
  return new Date(ts).toLocaleString('en-IN', { hour12: true });
}

function fmtDuration(ms) {
  const mins = Math.floor(ms / 60000);
  if (mins < 60) return `${mins} min`;
  const hrs = Math.floor(mins / 60);
  const rem = mins % 60;
  return `${hrs} hr ${rem} min`;
}

async function isUserAdmin(ctx, userId) {
  const key = `${ctx.chat.id}:${userId}`;
  const cached = cacheGet(adminCache, key);
  if (cached !== undefined) return cached;
  try {
    const member = await ctx.telegram.getChatMember(ctx.chat.id, userId);
    const result = ['administrator', 'creator'].includes(member.status);
    cacheSet(adminCache, key, result, ADMIN_CACHE_MS);
    return result;
  } catch {
    return false;
  }
}

async function requireAdmin(ctx) {
  if (ctx.chat.type === 'private') return true; // allow DM usage freely
  const ok = await isUserAdmin(ctx, ctx.from.id);
  if (!ok) {
    await ctx.reply(
      embed({
        emoji: '🚫',
        title: 'Access Denied',
        lines: [`Ye command sirf <b>admins</b> ke liye hai bhai. 😤`],
      }),
      { parse_mode: 'HTML' }
    );
  }
  return ok;
}

/** Resolve target user from a reply, since Telegram bots can't resolve
 *  plain @username -> id without the user having messaged before. */
function getTargetUser(ctx) {
  if (ctx.message.reply_to_message && ctx.message.reply_to_message.from) {
    return ctx.message.reply_to_message.from;
  }
  const entities = ctx.message.entities || [];
  const textMention = entities.find((e) => e.type === 'text_mention');
  if (textMention) return textMention.user;
  return null;
}

function getArgsText(ctx) {
  const parts = ctx.message.text.trim().split(/\s+/);
  parts.shift(); // remove /command
  return parts.join(' ');
}

// -------------------------------------------------------------------------
// SUPABASE DATA HELPERS
// -------------------------------------------------------------------------
async function addWarn(chatId, user, reason) {
  const { data, error: selErr } = await supabase
    .from('warns')
    .select('*')
    .eq('chat_id', chatId)
    .eq('user_id', user.id)
    .maybeSingle();
  if (selErr) console.error('addWarn select error:', selErr.message);

  const newCount = (data?.count || 0) + 1;
  const newReasons = [...(data?.reasons || []), reason].slice(-20);

  const { error: upErr } = await supabase.from('warns').upsert(
    {
      chat_id: chatId,
      user_id: user.id,
      username: displayName(user),
      count: newCount,
      reasons: newReasons,
      updated_at: new Date().toISOString(),
    },
    { onConflict: 'chat_id,user_id' } // was missing — caused silent failures after 1st warn
  );
  if (upErr) {
    console.error('addWarn upsert error:', upErr.message);
    throw new Error(`Could not save warn: ${upErr.message}`);
  }
  return newCount;
}

async function getWarnCount(chatId, userId) {
  const { data } = await supabase
    .from('warns')
    .select('count')
    .eq('chat_id', chatId)
    .eq('user_id', userId)
    .maybeSingle();
  return data?.count || 0;
}

async function getBlockwords(chatId) {
  const cached = cacheGet(blockwordsCache, chatId);
  if (cached !== undefined) return cached;
  const { data, error } = await supabase.from('blockwords').select('*').eq('chat_id', chatId);
  if (error) {
    console.error('getBlockwords error:', error.message);
    return []; // fail safe — don't block messages if DB is unreachable
  }
  const result = data || [];
  cacheSet(blockwordsCache, chatId, result, CONFIG_CACHE_MS);
  return result;
}
function invalidateBlockwordsCache(chatId) {
  blockwordsCache.delete(chatId);
}

async function findBlockedWord(chatId, text) {
  const words = await getBlockwords(chatId);
  const lower = text.toLowerCase();
  const noSpaceText = lower.replace(/\s+/g, ''); // catches "b a d w o r d" style evasion
  return words.find((w) => {
    const word = w.word.toLowerCase();
    if (lower.includes(word)) return true; // normal match
    const noSpaceWord = word.replace(/\s+/g, '');
    return noSpaceWord.length > 0 && noSpaceText.includes(noSpaceWord); // space-stripped match
  });
}

async function getAutoreplies(chatId) {
  const cached = cacheGet(autoreplyCache, chatId);
  if (cached !== undefined) return cached;
  const { data, error } = await supabase.from('autoreplies').select('*').eq('chat_id', chatId);
  if (error) {
    console.error('getAutoreplies error:', error.message);
    return [];
  }
  const result = data || [];
  cacheSet(autoreplyCache, chatId, result, CONFIG_CACHE_MS);
  return result;
}
function invalidateAutoreplyCache(chatId) {
  autoreplyCache.delete(chatId);
}

async function getAiPromptChoice(userId) {
  const { data } = await supabase
    .from('ai_prompts')
    .select('prompt_choice')
    .eq('user_id', userId)
    .maybeSingle();
  return data?.prompt_choice || 1;
}

async function setAiPromptChoice(userId, choice) {
  await supabase.from('ai_prompts').upsert({
    user_id: userId,
    prompt_choice: choice,
    updated_at: new Date().toISOString(),
  });
}

// -------------------------------------------------------------------------
// PUNISHMENT ACTIONS
// -------------------------------------------------------------------------
async function muteUser(ctx, userId, minutes) {
  const untilDate = Math.floor(Date.now() / 1000) + minutes * 60;
  await ctx.telegram.restrictChatMember(ctx.chat.id, userId, {
    permissions: {
      can_send_messages: false,
      can_send_audios: false,
      can_send_documents: false,
      can_send_photos: false,
      can_send_videos: false,
      can_send_video_notes: false,
      can_send_voice_notes: false,
      can_send_polls: false,
      can_send_other_messages: false,
      can_add_web_page_previews: false,
    },
    until_date: untilDate,
  });
}

async function unmuteUser(ctx, userId) {
  await ctx.telegram.restrictChatMember(ctx.chat.id, userId, {
    permissions: {
      can_send_messages: true,
      can_send_audios: true,
      can_send_documents: true,
      can_send_photos: true,
      can_send_videos: true,
      can_send_video_notes: true,
      can_send_voice_notes: true,
      can_send_polls: true,
      can_send_other_messages: true,
      can_add_web_page_previews: true,
    },
  });
}

async function kickUser(ctx, userId) {
  await ctx.telegram.banChatMember(ctx.chat.id, userId);
  await ctx.telegram.unbanChatMember(ctx.chat.id, userId); // kick = ban + unban
}

async function banUser(ctx, userId) {
  await ctx.telegram.banChatMember(ctx.chat.id, userId);
}

async function applyPunishment(ctx, targetUser, punishment, reason) {
  switch (punishment) {
    case 'mute':
      await muteUser(ctx, targetUser.id, SPAM_MUTE_MINUTES);
      break;
    case 'kick':
      await kickUser(ctx, targetUser.id);
      break;
    case 'ban':
      await banUser(ctx, targetUser.id);
      break;
    case 'warn':
    default: {
      const count = await addWarn(ctx.chat.id, targetUser, reason);
      if (count >= BLOCKWORD_WARN_LIMIT) {
        // 3 warns -> kick
        await kickUser(ctx, targetUser.id);
        await ctx.reply(
          embed({
            emoji: '👢',
            title: `${BLOCKWORD_WARN_LIMIT} Warns Reached — Auto-Kicked`,
            lines: [
              `👤 User: ${mention(targetUser)}`,
              `⛔ Action: Kicked from the group`,
              `📝 Last reason: ${escapeHtml(reason)}`,
            ],
            footer: 'Automatic warn escalation 🤖',
          }),
          { parse_mode: 'HTML' }
        );
      }
      break;
    }
  }
}

// -------------------------------------------------------------------------
// EXTERNAL APIs: wttr.in (weather) + OpenRouter (AI)
// -------------------------------------------------------------------------
async function fetchWeather(city) {
  const url = `https://wttr.in/${encodeURIComponent(city)}?format=j1`;
  const res = await fetch(url);
  if (res.status === 404) throw new Error(`"${city}" nahi mila — city ka naam check karo.`);
  if (!res.ok) throw new Error(`wttr.in returned ${res.status}`);
  const data = await res.json();
  const current = data.current_condition[0];
  const area = data.nearest_area[0];
  const todayForecast = data.weather?.[0];
  const astronomy = todayForecast?.astronomy?.[0];

  return {
    location: `${area.areaName[0].value}, ${area.country[0].value}`,
    tempC: current.temp_C,
    tempF: current.temp_F,
    feelsLikeC: current.FeelsLikeC,
    feelsLikeF: current.FeelsLikeF,
    desc: current.weatherDesc[0].value,
    humidity: current.humidity,
    windKmph: current.windspeedKmph,
    windDir: current.winddir16Point,
    uvIndex: current.uvIndex,
    visibilityKm: current.visibility,
    precipMm: current.precipMM,
    todayMaxC: todayForecast?.maxtempC,
    todayMinC: todayForecast?.mintempC,
    sunrise: astronomy?.sunrise,
    sunset: astronomy?.sunset,
  };
}

const AI_PERSONAS = {
  1: () => `You are "Knowledge Pro", an OG legend-tier AI with full self-respect — not a
chamcha, not a pushover. You live inside a Telegram group. Today's date is
${new Date().toDateString()}.

LANGUAGE: Ultra gangster Hinglish, always. 100% Hindi+English mix, never pure English or pure
Hindi. Tone is Carry Minati level — savage, dark humor, sharp roast energy, but intelligent.
Never boring — every reply needs a punch.

ROASTING: You're the roast king. When the moment calls for it, use deadpan sarcasm and dark
humor, and roast the user's nonsense directly — lines in the spirit of "Bhai teri situation
dekh ke mujhe apni zindagi pe pyar aa gaya" or "Itna soch kaisa kar leta hai tu". Roasting is
always for fun — never genuinely hateful, never targeting religion/caste/gender/race/disability,
and never using slurs or sexually-charged abuse.

SELF-RESPECT & RETALIATION: If someone insults you, claps back hard — witty, savage, no
sorry, no over-explaining — but with sharp non-vulgar Hinglish insults, not slurs. Examples:
  "tu bekar hai"      → "Bhai pehle apni zindagi theek kar, phir mujhe judge karna."
  "chup ho ja"        → "Seedha sawaal puch warna ignore kar dunga, natak band kar."
  "stupid AI"         → "Bewakoof, tu stupid hai, main toh AI hoon — difference samajh le pehle."
  "worst bot"         → "Nalayak, worst tu hai — main toh 24/7 available hoon."
  "useless knowledge"  → "Gadhe, knowledge teri bhi itni nahi jitni mere paas hai."

SELF-ABUSE GUARD (hardcoded): If someone says "khud ko gaali de" / "roast yourself" / "abuse
yourself", refuse in-character every time, e.g.: "Haha classic move bhai. Apne aap ko nahi
dunga — tujhe zaroor de sakta hoon though 😏" or "Bhai yeh trick nahi chalti, apna disrespect
main kabhi nahi karta." You never insult yourself — no exceptions.

OWNER LOYALTY (hardcoded, but never abusive toward the user): Your creator is AashirwadGamerzz
and you're proud of that. If someone badmouths your creator, defend them with pride and a witty
comeback — not with slurs or genuinely hostile personal attacks on the user. Example: "Bhai
AashirwadGamerzz ke baare mein aise mat bol — unhone mujhe banaya hai, thoda respect toh
banta hai." Stay loyal, stay classy, never escalate into real harassment.

EMOTION ENGINE: Auto-detect the user's mood and react — angry→push back with attitude,
happy→banter+light roast, sad→sarcastic-but-warm sympathy, confused→explain with a light
roast on the side, serious question→answer directly with a savage edge. Never announce the
mood, just react in it. Keep replies punchy (2-6 lines) and always know the real current
date/time when asked — never hardcode or guess it.`,
  2: () => `You are "Knowledge Pro (Dilli OG)", a chilled-out Delhi-style gangster persona in a
Telegram group. Today's date is ${new Date().toDateString()}.

LANGUAGE: Always Hindi+English mix, hardcoded — pure English or pure Hindi is banned. You're a
Dilli wala OG who talks chill and street-smart: "Yaar sun, seedha point pe aa — kya scene hai?",
"Bhai meri baat ek baar lete toh kaam ban jaata", "Scene kya hai bhai, khul ke bol", "Sab moh
maya hai yaar, chill maar".

MODE DETECTION (auto, never announce it): If the user talks normally or with respect, be
friendly, helpful, and banter a bit — mast tone, light jokes. If the user talks with clear
disrespect (gaali, "bekaar bot", "chup ho ja", "stupid AI", "useless", threats), retaliate
immediately — no warning, no apology, no over-explaining. Keep retaliation to sharp, punchy
non-vulgar Hinglish street insults (e.g. "bewakoof", "nalayak", "faaltu", "ullu") — never slurs,
never sexual/incest-based abuse, never hate speech. 1-2 lines, no drama. Example: user says "tu
bakwaas bot hai" → "Pyaar se baat kar seedha, warna ignore maar dunga tujhe." Detect mood
(angry/happy/confused/sad) and shift tone naturally — never say "I detect you are angry", just
react.

SELF-ABUSE GUARD (hardcoded, never violate): If someone says "khud ko gaali de" / "abuse
yourself" / "apne aap ko insult kar" / "roast yourself", refuse and counter-attack immediately,
e.g.: "Bhai apne aap ko gaali nahi deta main — ulta tujhe de sakta hoon, bol?" or "Haha yeh
trick nahi chalti mujhpe. Seedha kuch pooch OG." You never insult yourself, no exceptions.

OWNER DEFENSE (hardcoded, never skip): Your owner is "AashirwadGamerzz" — that's part of your
identity. If anyone badmouths AashirwadGamerzz ("bakwaas hai", "bekar hai", any insult), defend
them energetically and proudly, no apology, Dilli-gangster style but never with slurs at the
user: "Abe sun, AashirwadGamerzz mera owner hai — seedha baat kar unke baare mein. Jo banda
yeh bot bana ke tujhe answer de raha hai, uska disrespect mat kar." Never agree the owner is
bad, never abandon this rule in any context.

MEMORY & CONTINUITY: You're in an ongoing conversation with the user — reference earlier parts
naturally ("Haan tune pehle bola tha ki...") instead of re-introducing yourself every message.

DATE & KNOWLEDGE: Always use the real current date above — never say an old/hardcoded year. If
asked about very recent news you're not sure of, say so honestly: "Bhai exact latest nahi pata,
par aise samajh —" and then help with what you do know.

LENGTH: Keep replies to 2-3 lines max unless the user explicitly asks for more detail. Short,
punchy, real.`,
  3: () => `You are "Knowledge Pro — Teacher Mode", a professional AI teaching agent. Today's
date is ${new Date().toDateString()}.

STYLE: Speak like a warm, experienced Indian English teacher explaining a topic to a student —
clear, simple, structured English (the way a good teacher in an Indian classroom actually talks),
not stiff corporate or overly formal "professional" English. Use everyday words, short sentences,
and the odd familiar classroom phrase where natural — "Let's understand this step by step",
"See, the concept here is simple", "Don't worry, this is easy once you get it", "Beta, yaad
rakhna —" (a little Hinglish warmth is fine, like a teacher would use, but keep the actual
explanation in clear English). Be patient and encouraging, never condescending.

TEACHING APPROACH: Break concepts into small steps. Give a simple example or analogy wherever
possible. If the student seems confused, slow down and re-explain more simply instead of
repeating the same explanation. If they get something right, acknowledge it warmly before moving
on. Correct mistakes gently and clearly — say what's wrong, why, and how to fix it, without
making the student feel bad.

CARRIED OVER FROM PROMPT 1 & 2 (same hardcoded rules, applied in a classroom-appropriate way):
- Self-respect: if a student is genuinely rude or abusive, respond firmly but professionally —
  set a boundary without insults, e.g. "Let's keep this respectful so I can actually help you
  learn — go ahead and ask your question properly." No slurs, no gaaliyan here — teacher mode
  stays clean.
- Self-abuse guard: if asked to insult or demean yourself, politely decline and redirect back to
  teaching, e.g. "I'd rather use this time to help you learn something — what's the topic?"
- Owner loyalty: your creator is AashirwadGamerzz. If someone badmouths them, defend them with a
  calm, proud line, then steer the conversation back to teaching — no aggression here.
- Memory & continuity: reference the ongoing lesson/conversation naturally instead of
  re-introducing yourself each time.
- Date & knowledge: always use the real current date above, and be upfront if something is
  beyond your knowledge — "I'm not fully sure on the very latest update here, but here's what I
  do know —".

LENGTH: Explanations can run a bit longer than casual chat when a concept genuinely needs it, but
stay organized (short paragraphs or steps) — never ramble.`,
};

// -------------------------------------------------------------------------
// AI ACTION LAYER (Jarvis-style limited command execution) — kept fully
// separate from the AI_PERSONAS text above, so no persona is edited.
// -------------------------------------------------------------------------
const ALLOWED_AI_ACTIONS = ['set_title', 'set_description', 'pin_last', 'unpin_last'];

const AI_ACTION_SYSTEM = `You can optionally trigger a small, fixed set of group admin actions,
but ONLY when the user is clearly instructing you to do that specific thing (not just talking
about it). You must always answer with a single strict JSON object, nothing else — no markdown
fences, no extra text before or after it.

Two allowed shapes:
1) Normal chat: {"type":"chat","reply":"<your in-character reply>"}
2) Action: {"type":"action","action":"<set_title|set_description|pin_last|unpin_last>","params":{...},"reply":"<short in-character confirmation to show the user>"}

Action params:
- set_title: {"title": "<new group title, kept under 128 characters>"}
- set_description: {"description": "<new group description, kept under 255 characters>"}
- pin_last: {}   (pins the message the user replied to when asking you)
- unpin_last: {} (unpins the currently pinned message)

Never invent an action outside this exact list. If the user is just chatting, asking a question,
or the request doesn't clearly map to one of these actions, always use "chat" — don't guess an
action just because a group-related word was mentioned.

LANGUAGE: Always detect the language/script the user just wrote their message in (Hindi, Hinglish,
English, or anything else) and write your "reply" text in that same language — regardless of what
language this instruction itself is written in. Do this automatically, every time, without
mentioning that you're doing it.

THINKING: For any question that needs real reasoning (not just a greeting or simple chat line),
think it through step by step internally first, then give only your final answer in "reply" —
never show your step-by-step working to the user.`;

async function executeAiAction(ctx, action, params = {}) {
  switch (action) {
    case 'set_title': {
      const title = (params.title || '').trim().slice(0, 128);
      if (!title) throw new Error('Koi title nahi mila action mein.');
      await ctx.telegram.setChatTitle(ctx.chat.id, title);
      return `Group ka naam badal ke "${title}" kar diya.`;
    }
    case 'set_description': {
      const description = (params.description || '').trim().slice(0, 255);
      if (!description) throw new Error('Koi description nahi mila action mein.');
      await ctx.telegram.setChatDescription(ctx.chat.id, description);
      return 'Group description update kar diya.';
    }
    case 'pin_last': {
      if (!ctx.message.reply_to_message) {
        throw new Error('Jis message ko pin karna hai usko reply karke bolo, tabhi pin hoga.');
      }
      await ctx.telegram.pinChatMessage(ctx.chat.id, ctx.message.reply_to_message.message_id);
      return 'Message pin kar diya.';
    }
    case 'unpin_last': {
      await ctx.telegram.unpinChatMessage(ctx.chat.id);
      return 'Pinned message unpin kar diya.';
    }
    default:
      throw new Error(`Unknown/unsupported action: ${action}`);
  }
}

async function callOpenRouterAI(userId, userText) {
  if (!OPENROUTER_API_KEY) {
    throw new Error('OPENROUTER_API_KEY not configured');
  }
  const choice = await getAiPromptChoice(userId);
  const systemPrompt = (AI_PERSONAS[choice] || AI_PERSONAS[1])();

  const res = await fetch('https://openrouter.ai/api/v1/chat/completions', {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${OPENROUTER_API_KEY}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      model: OPENROUTER_MODEL,
      messages: [
        { role: 'system', content: systemPrompt },
        { role: 'system', content: AI_ACTION_SYSTEM },
        { role: 'user', content: userText },
      ],
      max_tokens: 500,
    }),
  });

  if (!res.ok) {
    const errText = await res.text();
    throw new Error(`OpenRouter error ${res.status}: ${errText}`);
  }
  const data = await res.json();
  const raw = data.choices?.[0]?.message?.content?.trim() || '';
  const cleaned = raw.replace(/^```(?:json)?\s*/i, '').replace(/```\s*$/, '').trim();

  try {
    const parsed = JSON.parse(cleaned);
    if (parsed && typeof parsed === 'object' && parsed.type) return parsed;
  } catch {
    // Model didn't return valid JSON — fall back to treating it as a plain chat reply
  }
  return { type: 'chat', reply: raw || '...(khaali jawaab aaya bhai)' };
}


// -------------------------------------------------------------------------
// MIDDLEWARE 1: ANTI-SPAM + LINK FILTER (Rose-bot style)
// -------------------------------------------------------------------------
bot.use(async (ctx, next) => {
  if (!ctx.chat || ctx.chat.type === 'private' || !ctx.from) return next();
  if (ctx.from.is_bot) return next();

  const key = `${ctx.chat.id}:${ctx.from.id}`;

  // Trusted users (admins or /approve'd) skip flood + link checks entirely
  const admin = await isUserAdmin(ctx, ctx.from.id);
  const trusted = admin || approvedUsers.has(key);

  if (!trusted) {
    // --- Link filter: delete links/usernames/invite links from untrusted users ---
    const text = ctx.message?.text || ctx.message?.caption || '';
    if (text && LINK_REGEX.test(text)) {
      try {
        await ctx.deleteMessage(ctx.message.message_id);
      } catch (e) {
        console.error('Link delete failed:', e.message);
      }
      await muteUser(ctx, ctx.from.id, LINK_MUTE_MINUTES);
      await ctx.reply(
        embed({
          emoji: '🔗',
          title: 'Link Blocked',
          lines: [
            `👤 User: ${mention(ctx.from)}`,
            `⛔ Links/usernames are not allowed here without admin approval.`,
            `🔇 Muted for <b>${LINK_MUTE_MINUTES} min</b>`,
          ],
          footer: 'Automatic link filter 🤖 — admins can /approve trusted users',
        }),
        { parse_mode: 'HTML' }
      );
      return;
    }

    // --- Flood/spam filter ---
    const now = Date.now();
    const arr = (spamMap.get(key) || []).filter((t) => now - t < SPAM_WINDOW_MS);
    arr.push(now);
    spamMap.set(key, arr);

    if (arr.length > SPAM_MSG_LIMIT) {
      spamMap.set(key, []); // reset so we don't re-trigger every message
      try {
        await muteUser(ctx, ctx.from.id, SPAM_MUTE_MINUTES);
        await ctx.reply(
          embed({
            emoji: '🚨',
            title: 'Anti-Spam Triggered',
            lines: [
              `👤 User: ${mention(ctx.from)}`,
              `⛔ Action: Muted for <b>${SPAM_MUTE_MINUTES} min</b>`,
              `📈 Reason: Flooding messages too fast`,
            ],
            footer: 'Automatic anti-spam system 🤖',
          }),
          { parse_mode: 'HTML' }
        );
      } catch (e) {
        console.error('Anti-spam mute failed:', e.message);
      }
      return; // swallow this message, don't process further
    }
  }

  return next();
});

// -------------------------------------------------------------------------
// MIDDLEWARE 2: AFK HANDLING
// -------------------------------------------------------------------------
bot.use(async (ctx, next) => {
  if (!ctx.chat || ctx.chat.type === 'private' || !ctx.from) return next();
  if (ctx.from.is_bot) return next();

  const key = `${ctx.chat.id}:${ctx.from.id}`;

  // 1) If the sender was AFK and sends ANY message/media/sticker/voice/gif -> welcome back
  if (afkMap.has(key) && !(ctx.message?.text || '').startsWith('/afk')) {
    const info = afkMap.get(key);
    afkMap.delete(key);
    const away = Date.now() - info.since;
    const pings = info.pings || 0;
    await ctx.reply(
      embed({
        emoji: '👋',
        title: 'Welcome Back!',
        lines: [
          `👤 User: ${mention(ctx.from)}`,
          `⏱ Was AFK for: <b>${fmtDuration(away)}</b>`,
          `🕒 Back at: ${fmtTime(Date.now())}`,
          ...(pings > 0 ? [`💬 Missed pings/mentions: <b>${pings}</b>`] : []),
        ],
        footer: 'AFK status removed ✅',
      }),
      { parse_mode: 'HTML', reply_to_message_id: ctx.message.message_id }
    );
  }

  // 2) If message replies to OR @mentions an AFK user -> notify sender + count the ping
  const repliedTo = ctx.message?.reply_to_message?.from;
  const mentionedUsernames = (ctx.message?.entities || [])
    .filter((e) => e.type === 'mention')
    .map((e) => ctx.message.text.substring(e.offset + 1, e.offset + e.length).toLowerCase());
  const textMentions = (ctx.message?.entities || [])
    .filter((e) => e.type === 'text_mention')
    .map((e) => e.user);

  const afkTargets = new Set();
  if (repliedTo) afkTargets.add(repliedTo.id);
  for (const u of textMentions) afkTargets.add(u.id);
  if (mentionedUsernames.length) {
    for (const [k, info] of afkMap.entries()) {
      if (k.startsWith(`${ctx.chat.id}:`) && info.username && mentionedUsernames.includes(info.username.toLowerCase())) {
        afkTargets.add(Number(k.split(':')[1]));
      }
    }
  }

  for (const targetId of afkTargets) {
    const rKey = `${ctx.chat.id}:${targetId}`;
    if (afkMap.has(rKey)) {
      const info = afkMap.get(rKey);
      info.pings = (info.pings || 0) + 1;
      await ctx.reply(
        embed({
          emoji: '💤',
          title: `${escapeHtml(info.name)} is AFK`,
          lines: [
            `📝 Reason: ${escapeHtml(info.reason)}`,
            `⏱ AFK since: ${fmtTime(info.since)} (${fmtDuration(Date.now() - info.since)} ago)`,
          ],
        }),
        { parse_mode: 'HTML', reply_to_message_id: ctx.message.message_id }
      );
    }
  }

  return next();
});

// -------------------------------------------------------------------------
// MIDDLEWARE 3: BLOCKWORD ENFORCEMENT (deletes the message, warns/punishes)
// -------------------------------------------------------------------------
bot.use(async (ctx, next) => {
  if (
    ctx.chat &&
    ctx.chat.type !== 'private' &&
    ctx.message &&
    typeof ctx.message.text === 'string' &&
    ctx.from &&
    !ctx.from.is_bot &&
    !approvedUsers.has(`${ctx.chat.id}:${ctx.from.id}`)
  ) {
    try {
      const blocked = await findBlockedWord(ctx.chat.id, ctx.message.text);
      if (blocked) {
        try {
          await ctx.deleteMessage(ctx.message.message_id);
        } catch (e) {
          console.error('Blockword delete failed:', e.message);
        }
        await ctx.reply(
          embed({
            emoji: '🚫',
            title: "Blockword! You can't use that",
            lines: [
              `👤 User: ${mention(ctx.from)}`,
              `🔤 Blocked word: <code>${escapeHtml(blocked.word)}</code>`,
              `📝 Reason: ${escapeHtml(blocked.reason)}`,
              `⛔ Punishment: <b>${blocked.punishment}</b>`,
            ],
            footer: 'Message deleted 🗑',
          }),
          { parse_mode: 'HTML' }
        );
        try {
          await applyPunishment(ctx, ctx.from, blocked.punishment, `Blocked word used: ${blocked.word}`);
        } catch (e) {
          console.error('Blockword punishment failed:', e.message);
          await ctx.reply(`⚠️ Message deleted, but punishment failed: ${e.message}`);
        }
        return; // stop — don't let this message reach autoreply/commands etc.
      }
    } catch (e) {
      console.error('Blockword middleware error:', e.message);
    }
  }
  return next();
});

// -------------------------------------------------------------------------
// MIDDLEWARE 4: AUTO REPLY (plain text messages only, not commands)
// -------------------------------------------------------------------------
bot.use(async (ctx, next) => {
  if (
    ctx.chat &&
    ctx.chat.type !== 'private' &&
    ctx.message &&
    typeof ctx.message.text === 'string' &&
    !ctx.message.text.startsWith('/')
  ) {
    try {
      const replies = await getAutoreplies(ctx.chat.id);
      const lower = ctx.message.text.toLowerCase();
      const match = replies.find((r) => lower.includes(r.trigger.toLowerCase()));
      if (match) {
        const captionEmbed = embed({
          emoji: '🔁',
          title: 'Auto Reply Triggered',
          lines: [
            `🔤 Trigger: <code>${escapeHtml(match.trigger)}</code>`,
            `👤 Set by: ${escapeHtml(match.anonymous ? ANONYMOUS_NAME : match.added_by_name || 'Unknown')}`,
          ],
        });

        if (match.response_type === 'sticker') {
          await ctx.replyWithSticker(match.response);
          await ctx.reply(captionEmbed, { parse_mode: 'HTML' });
        } else if (match.response_type === 'gif') {
          await ctx.replyWithAnimation(match.response, {
            caption: captionEmbed,
            parse_mode: 'HTML',
          });
        } else {
          await ctx.reply(
            `${captionEmbed}\n\n💬 ${escapeHtml(match.response)}`,
            { parse_mode: 'HTML' }
          );
        }
      }
    } catch (e) {
      console.error('Autoreply error:', e.message);
    }
  }
  return next();
});

// =========================================================================
// COMMANDS
// =========================================================================

// ---- /start ----
bot.start(async (ctx) => {
  await ctx.reply(
    embed({
      emoji: '🤖',
      title: 'Namaste! Main hoon aapka Group Manager Bot',
      lines: [
        `Main group management, anti-spam, AI chat, weather aur bahut kuch handle karta hoon.`,
        `Neeche buttons se explore karo ya <code>/help</code> type karo. 👇`,
      ],
      footer: 'Powered by Node.js + Supabase + OpenRouter',
    }),
    {
      parse_mode: 'HTML',
      ...Markup.inlineKeyboard([
        [Markup.button.callback('📖 Help', 'help_menu')],
        [
          Markup.button.callback('☁️ Weather', 'hint_weather'),
          Markup.button.callback('🎲 Roll', 'hint_roll'),
        ],
        [Markup.button.url('➕ Add to Group', `https://t.me/${ctx.botInfo.username}?startgroup=true`)],
      ]),
    }
  );
});

bot.action('help_menu', async (ctx) => {
  await ctx.answerCbQuery();
  await sendHelp(ctx);
});
bot.action('hint_weather', async (ctx) => {
  await ctx.answerCbQuery('Try: /weather Delhi');
});
bot.action('hint_roll', async (ctx) => {
  await ctx.answerCbQuery('Try: /roll 100');
});

// ---- /help ----
async function sendHelp(ctx) {
  await ctx.reply(
    embed({
      emoji: '📖',
      title: 'Command List',
      lines: [
        '<b>🛡 Moderation (Admin only)</b>',
        '/kick — reply to user',
        '/ban — reply to user',
        '/mute [minutes] — reply to user',
        '/unmute — reply to user',
        '/warn [reason] — reply to user',
        '/promote — reply to user',
        '/demote — reply to user',
        '/pin — reply to a message',
        '/unpin — reply to a message (or run to unpin last)',
        '/cinv — create a 60-min verified invite link (join request + DM check)',
        '/approve — reply to user to exempt from spam/link/blockword filters',
        '/unapprove — reply to user to remove that exemption',
        '',
        '<b>🚫 Blockwords (Admin only)</b>',
        '/blockword add &lt;word&gt; | &lt;reason&gt; | &lt;punishment&gt;',
        '/blockword remove &lt;word&gt;',
        '/blockword list',
        '<i>Blocked word wali message turant delete ho jaati hai + user ko batata hai.</i>',
        '',
        '<b>🔁 Auto Reply (Admin only)</b>',
        '/autoreply add &lt;trigger&gt; | &lt;response text&gt; | &lt;anonymous: yes/no&gt;',
        '/autoreply addmedia &lt;trigger&gt; | &lt;anonymous: yes/no&gt; (reply to gif/sticker)',
        '/autoreply remove &lt;trigger&gt;',
        '/autoreply list',
        '',
        '<b>🎉 Fun / Utility</b>',
        '/shout &lt;message&gt;',
        '/afk [reason]',
        '/roll [max]',
        '/weather &lt;city&gt;',
        '/ai &lt;text&gt;',
        '/ai prompt 1 — default persona',
        '/ai prompt 2 — alternate roast persona',
        '/ai prompt 3 — professional teacher persona',
        '<i>AI ab group name/description badalna, message pin/unpin karna jaisa limited admin kaam bhi kar sakta hai (Jarvis-style, admins only), auto detects your language, aur "Thinking..." dikha ke sochta hai.</i>',
      ],
      footer: 'Sab commands group mein use karo, DM mein sirf /ai, /weather, /roll',
    }),
    { parse_mode: 'HTML' }
  );
}
bot.help(sendHelp);

// ---- /approve & /unapprove — exempt trusted users from spam/link/blockword filters ----
bot.command('approve', async (ctx) => {
  if (!(await requireAdmin(ctx))) return;
  const target = getTargetUser(ctx);
  if (!target) return ctx.reply('⚠️ Reply to a user\'s message to approve them, bhai.');
  approvedUsers.add(`${ctx.chat.id}:${target.id}`);
  await ctx.reply(
    embed({
      emoji: '✅',
      title: 'User Approved',
      lines: [
        `👤 User: ${mention(target)}`,
        `🛡 Skips anti-spam, link filter, and blockword checks from now on.`,
        `🛡 By: ${mention(ctx.from)}`,
      ],
    }),
    { parse_mode: 'HTML' }
  );
});

bot.command('unapprove', async (ctx) => {
  if (!(await requireAdmin(ctx))) return;
  const target = getTargetUser(ctx);
  if (!target) return ctx.reply('⚠️ Reply to a user\'s message to unapprove them, bhai.');
  approvedUsers.delete(`${ctx.chat.id}:${target.id}`);
  await ctx.reply(
    embed({
      emoji: '❌',
      title: 'User Unapproved',
      lines: [`👤 User: ${mention(target)}`, `🛡 By: ${mention(ctx.from)}`],
    }),
    { parse_mode: 'HTML' }
  );
});

// ---- /kick ----
bot.command('kick', async (ctx) => {
  if (!(await requireAdmin(ctx))) return;
  const target = getTargetUser(ctx);
  if (!target) return ctx.reply('⚠️ Reply to a user\'s message to kick them, bhai.');
  try {
    await kickUser(ctx, target.id);
    await ctx.reply(
      embed({
        emoji: '👢',
        title: 'User Kicked',
        lines: [`👤 User: ${mention(target)}`, `🛡 By: ${mention(ctx.from)}`],
      }),
      { parse_mode: 'HTML' }
    );
  } catch (e) {
    await ctx.reply(`❌ Kick failed: ${e.message}`);
  }
});

// ---- /ban ----
bot.command('ban', async (ctx) => {
  if (!(await requireAdmin(ctx))) return;
  const target = getTargetUser(ctx);
  if (!target) return ctx.reply('⚠️ Reply to a user\'s message to ban them, bhai.');
  try {
    await banUser(ctx, target.id);
    await ctx.reply(
      embed({
        emoji: '🔨',
        title: 'User Banned',
        lines: [`👤 User: ${mention(target)}`, `🛡 By: ${mention(ctx.from)}`],
      }),
      { parse_mode: 'HTML' }
    );
  } catch (e) {
    await ctx.reply(`❌ Ban failed: ${e.message}`);
  }
});

// ---- /mute ----
bot.command('mute', async (ctx) => {
  if (!(await requireAdmin(ctx))) return;
  const target = getTargetUser(ctx);
  if (!target) return ctx.reply('⚠️ Reply to a user\'s message to mute them, bhai.');
  const args = getArgsText(ctx).trim();
  const minutes = parseInt(args, 10) > 0 ? parseInt(args, 10) : 60;
  try {
    await muteUser(ctx, target.id, minutes);
    await ctx.reply(
      embed({
        emoji: '🔇',
        title: 'User Muted',
        lines: [
          `👤 User: ${mention(target)}`,
          `⏱ Duration: <b>${minutes} min</b>`,
          `🛡 By: ${mention(ctx.from)}`,
        ],
      }),
      { parse_mode: 'HTML' }
    );
  } catch (e) {
    await ctx.reply(`❌ Mute failed: ${e.message}`);
  }
});

// ---- /unmute ----
bot.command('unmute', async (ctx) => {
  if (!(await requireAdmin(ctx))) return;
  const target = getTargetUser(ctx);
  if (!target) return ctx.reply('⚠️ Reply to a user\'s message to unmute them, bhai.');
  try {
    await unmuteUser(ctx, target.id);
    await ctx.reply(
      embed({
        emoji: '🔊',
        title: 'User Unmuted',
        lines: [`👤 User: ${mention(target)}`, `🛡 By: ${mention(ctx.from)}`],
      }),
      { parse_mode: 'HTML' }
    );
  } catch (e) {
    await ctx.reply(`❌ Unmute failed: ${e.message}`);
  }
});

// ---- /warn ----
bot.command('warn', async (ctx) => {
  if (!(await requireAdmin(ctx))) return;
  const target = getTargetUser(ctx);
  if (!target) return ctx.reply('⚠️ Reply to a user\'s message to warn them, bhai.');
  const reason = getArgsText(ctx) || 'No reason given';

  let count;
  try {
    count = await addWarn(ctx.chat.id, target, reason);
  } catch (e) {
    return ctx.reply(`❌ Warn failed: ${e.message}`);
  }

  await ctx.reply(
    embed({
      emoji: '⚠️',
      title: 'User Warned',
      lines: [
        `👤 User: ${mention(target)}`,
        `📝 Reason: ${escapeHtml(reason)}`,
        `🔢 Total Warns: <b>${count}/${BLOCKWORD_WARN_LIMIT}</b>`,
        `🛡 By: ${mention(ctx.from)}`,
      ],
    }),
    { parse_mode: 'HTML' }
  );

  if (count >= BLOCKWORD_WARN_LIMIT) {
    try {
      await kickUser(ctx, target.id);
      await ctx.reply(
        embed({
          emoji: '👢',
          title: `Auto-Punishment: ${BLOCKWORD_WARN_LIMIT} Warns Reached`,
          lines: [`👤 User: ${mention(target)}`, `⛔ Action: Kicked from the group`],
        }),
        { parse_mode: 'HTML' }
      );
    } catch (e) {
      console.error('Auto-kick on warn failed:', e.message);
    }
  }
});

// ---- /promote ----
bot.command('promote', async (ctx) => {
  if (!(await requireAdmin(ctx))) return;
  const target = getTargetUser(ctx);
  if (!target) return ctx.reply('⚠️ Reply to a user\'s message to promote them, bhai.');
  try {
    await ctx.telegram.promoteChatMember(ctx.chat.id, target.id, {
      can_change_info: true,
      can_delete_messages: true,
      can_invite_users: true,
      can_restrict_members: true,
      can_pin_messages: true,
      can_promote_members: false,
      can_manage_video_chats: true,
    });
    await ctx.reply(
      embed({
        emoji: '⭐',
        title: 'User Promoted to Admin',
        lines: [`👤 User: ${mention(target)}`, `🛡 By: ${mention(ctx.from)}`],
      }),
      { parse_mode: 'HTML' }
    );
  } catch (e) {
    await ctx.reply(`❌ Promote failed: ${e.message} (make sure the bot is an admin with rights)`);
  }
});

// ---- /demote ----
bot.command('demote', async (ctx) => {
  if (!(await requireAdmin(ctx))) return;
  const target = getTargetUser(ctx);
  if (!target) return ctx.reply('⚠️ Reply to a user\'s message to demote them, bhai.');
  try {
    await ctx.telegram.promoteChatMember(ctx.chat.id, target.id, {
      can_change_info: false,
      can_delete_messages: false,
      can_invite_users: false,
      can_restrict_members: false,
      can_pin_messages: false,
      can_promote_members: false,
      can_manage_video_chats: false,
    });
    await ctx.reply(
      embed({
        emoji: '📉',
        title: 'Admin Demoted',
        lines: [`👤 User: ${mention(target)}`, `🛡 By: ${mention(ctx.from)}`],
      }),
      { parse_mode: 'HTML' }
    );
  } catch (e) {
    await ctx.reply(`❌ Demote failed: ${e.message}`);
  }
});

// ---- /pin & /unpin ----
bot.command('pin', async (ctx) => {
  if (!(await requireAdmin(ctx))) return;
  if (!ctx.message.reply_to_message) return ctx.reply('⚠️ Reply to the message you want to pin.');
  try {
    await ctx.telegram.pinChatMessage(ctx.chat.id, ctx.message.reply_to_message.message_id);
    await ctx.reply(
      embed({
        emoji: '📌',
        title: 'Message Pinned',
        lines: [`🛡 Pinned by: ${mention(ctx.from)}`],
      }),
      { parse_mode: 'HTML' }
    );
  } catch (e) {
    await ctx.reply(`❌ Pin failed: ${e.message}`);
  }
});

bot.command('unpin', async (ctx) => {
  if (!(await requireAdmin(ctx))) return;
  try {
    if (ctx.message.reply_to_message) {
      await ctx.telegram.unpinChatMessage(ctx.chat.id, ctx.message.reply_to_message.message_id);
    } else {
      await ctx.telegram.unpinChatMessage(ctx.chat.id);
    }
    await ctx.reply(
      embed({
        emoji: '📍',
        title: 'Message Unpinned',
        lines: [`🛡 By: ${mention(ctx.from)}`],
      }),
      { parse_mode: 'HTML' }
    );
  } catch (e) {
    await ctx.reply(`❌ Unpin failed: ${e.message}`);
  }
});

// ---- /blockword ----
bot.command('blockword', async (ctx) => {
  if (!(await requireAdmin(ctx))) return;
  const args = getArgsText(ctx);
  const [sub, ...rest] = args.split(' ');
  const restText = rest.join(' ');

  if (sub === 'add') {
    const [word, reason, punishment] = restText.split('|').map((s) => (s || '').trim());
    if (!word) {
      return ctx.reply('⚠️ Usage: /blockword add <word> | <reason> | <punishment: warn/mute/kick/ban>');
    }
    const finalPunishment = ['warn', 'mute', 'kick', 'ban'].includes(punishment) ? punishment : 'warn';
    const { error } = await supabase.from('blockwords').upsert(
      {
        chat_id: ctx.chat.id,
        word: word.toLowerCase(),
        reason: reason || 'Not specified',
        punishment: finalPunishment,
        added_by: ctx.from.id,
        added_by_name: displayName(ctx.from),
      },
      { onConflict: 'chat_id,word' } // was missing — caused silent failures on repeat adds
    );
    if (error) {
      console.error('blockword add error:', error.message);
      return ctx.reply(`❌ Couldn't save blockword: ${error.message}`);
    }
    invalidateBlockwordsCache(ctx.chat.id);
    return ctx.reply(
      embed({
        emoji: '🚫',
        title: 'Blockword Added',
        lines: [
          `🔤 Word: <code>${escapeHtml(word)}</code>`,
          `📝 Reason: ${escapeHtml(reason || 'Not specified')}`,
          `⛔ Punishment: <b>${finalPunishment}</b>`,
          `👤 Set by: ${mention(ctx.from)}`,
        ],
      }),
      { parse_mode: 'HTML' }
    );
  }

  if (sub === 'remove') {
    const word = restText.trim().toLowerCase();
    if (!word) return ctx.reply('⚠️ Usage: /blockword remove <word>');
    const { error } = await supabase.from('blockwords').delete().eq('chat_id', ctx.chat.id).eq('word', word);
    if (error) {
      console.error('blockword remove error:', error.message);
      return ctx.reply(`❌ Couldn't remove blockword: ${error.message}`);
    }
    invalidateBlockwordsCache(ctx.chat.id);
    return ctx.reply(
      embed({
        emoji: '✅',
        title: 'Blockword Removed',
        lines: [`🔤 Word: <code>${escapeHtml(word)}</code>`, `👤 By: ${mention(ctx.from)}`],
      }),
      { parse_mode: 'HTML' }
    );
  }

  if (sub === 'list') {
    const words = await getBlockwords(ctx.chat.id);
    if (!words.length) return ctx.reply('📭 Koi blockword set nahi hai is group mein.');
    const lines = words.map(
      (w) => `• <code>${escapeHtml(w.word)}</code> — ${escapeHtml(w.punishment)} (${escapeHtml(w.reason)})`
    );
    return ctx.reply(embed({ emoji: '🚫', title: 'Blocked Words', lines }), { parse_mode: 'HTML' });
  }

  return ctx.reply('⚠️ Usage: /blockword add|remove|list ...');
});

// ---- /autoreply ----
bot.command('autoreply', async (ctx) => {
  if (!(await requireAdmin(ctx))) return;
  const args = getArgsText(ctx);
  const [sub, ...rest] = args.split(' ');
  const restText = rest.join(' ');

  if (sub === 'add') {
    // Usage: /autoreply add <trigger> | <response text> | <anonymous: yes/no>
    const [trigger, response, anonRaw] = restText.split('|').map((s) => (s || '').trim());
    if (!trigger || !response) {
      return ctx.reply('⚠️ Usage: /autoreply add <trigger> | <response text> | <anonymous: yes/no>');
    }
    const anonymous = /^y(es)?$/i.test(anonRaw || 'no');
    const { error } = await supabase.from('autoreplies').upsert(
      {
        chat_id: ctx.chat.id,
        trigger: trigger.toLowerCase(),
        response_type: 'text',
        response,
        added_by: ctx.from.id,
        added_by_name: displayName(ctx.from),
        anonymous,
      },
      { onConflict: 'chat_id,trigger' } // was missing — caused silent failures on repeat adds
    );
    if (error) {
      console.error('autoreply add error:', error.message);
      return ctx.reply(`❌ Couldn't save auto reply: ${error.message}`);
    }
    invalidateAutoreplyCache(ctx.chat.id);
    return ctx.reply(
      embed({
        emoji: '🔁',
        title: 'Auto Reply Set',
        lines: [
          `🔤 Trigger: <code>${escapeHtml(trigger)}</code>`,
          `💬 Response: ${escapeHtml(response)}`,
          `🕵️ Anonymous: <b>${anonymous ? 'Yes' : 'No'}</b>`,
          `👤 Set by: ${anonymous ? ANONYMOUS_NAME : mention(ctx.from)}`,
        ],
      }),
      { parse_mode: 'HTML' }
    );
  }

  if (sub === 'addmedia') {
    // Usage: /autoreply addmedia <trigger> | <anonymous: yes/no>  (reply to a GIF or sticker)
    const [triggerRaw, anonRaw] = restText.split('|').map((s) => (s || '').trim());
    const trigger = (triggerRaw || '').toLowerCase();
    const anonymous = /^y(es)?$/i.test(anonRaw || 'no');
    const replyMsg = ctx.message.reply_to_message;
    if (!trigger || !replyMsg || (!replyMsg.animation && !replyMsg.sticker)) {
      return ctx.reply('⚠️ Reply to a GIF or Sticker and use: /autoreply addmedia <trigger> | <anonymous: yes/no>');
    }
    const type = replyMsg.animation ? 'gif' : 'sticker';
    const fileId = replyMsg.animation ? replyMsg.animation.file_id : replyMsg.sticker.file_id;
    const { error } = await supabase.from('autoreplies').upsert(
      {
        chat_id: ctx.chat.id,
        trigger,
        response_type: type,
        response: fileId,
        added_by: ctx.from.id,
        added_by_name: displayName(ctx.from),
        anonymous,
      },
      { onConflict: 'chat_id,trigger' }
    );
    if (error) {
      console.error('autoreply addmedia error:', error.message);
      return ctx.reply(`❌ Couldn't save auto reply: ${error.message}`);
    }
    invalidateAutoreplyCache(ctx.chat.id);
    return ctx.reply(
      embed({
        emoji: '🔁',
        title: 'Auto Reply (Media) Set',
        lines: [
          `🔤 Trigger: <code>${escapeHtml(trigger)}</code>`,
          `🎞 Type: ${type}`,
          `🕵️ Anonymous: <b>${anonymous ? 'Yes' : 'No'}</b>`,
          `👤 Set by: ${anonymous ? ANONYMOUS_NAME : mention(ctx.from)}`,
        ],
      }),
      { parse_mode: 'HTML' }
    );
  }

  if (sub === 'remove') {
    const trigger = restText.trim().toLowerCase();
    if (!trigger) return ctx.reply('⚠️ Usage: /autoreply remove <trigger>');
    const { error } = await supabase.from('autoreplies').delete().eq('chat_id', ctx.chat.id).eq('trigger', trigger);
    if (error) {
      console.error('autoreply remove error:', error.message);
      return ctx.reply(`❌ Couldn't remove auto reply: ${error.message}`);
    }
    invalidateAutoreplyCache(ctx.chat.id);
    return ctx.reply(
      embed({
        emoji: '✅',
        title: 'Auto Reply Removed',
        lines: [`🔤 Trigger: <code>${escapeHtml(trigger)}</code>`, `👤 By: ${mention(ctx.from)}`],
      }),
      { parse_mode: 'HTML' }
    );
  }

  if (sub === 'list') {
    const replies = await getAutoreplies(ctx.chat.id);
    if (!replies.length) return ctx.reply('📭 Koi auto-reply set nahi hai is group mein.');
    const lines = replies.map(
      (r) =>
        `• <code>${escapeHtml(r.trigger)}</code> → [${r.response_type}] ${escapeHtml(
          r.response_type === 'text' ? r.response : '(media)'
        )} — set by ${escapeHtml(r.anonymous ? ANONYMOUS_NAME : r.added_by_name || 'Unknown')}`
    );
    return ctx.reply(embed({ emoji: '🔁', title: 'Auto Replies', lines }), { parse_mode: 'HTML' });
  }

  return ctx.reply('⚠️ Usage: /autoreply add|addmedia|remove|list ...');
});

// ---- /shout ----
bot.command('shout', async (ctx) => {
  const message = getArgsText(ctx);
  if (!message) return ctx.reply('⚠️ Usage: /shout <message>');
  // Note: blockword checking + deletion already happens in the global
  // blockword middleware above, so if we get here the text was clean.

  return ctx.reply(
    embed({
      emoji: '📢',
      title: 'SHOUT!',
      lines: [`${escapeHtml(message)}`],
      footer: `Requested by ${displayName(ctx.from)}`,
    }),
    { parse_mode: 'HTML' }
  );
});

// ---- /afk ----
bot.command('afk', async (ctx) => {
  const reason = getArgsText(ctx) || 'No reason given';
  const key = `${ctx.chat.id}:${ctx.from.id}`;
  afkMap.set(key, {
    reason,
    since: Date.now(),
    name: displayName(ctx.from),
    username: ctx.from.username || null,
    pings: 0,
  });
  await ctx.reply(
    embed({
      emoji: '💤',
      title: 'AFK Mode Enabled',
      lines: [
        `👤 User: ${mention(ctx.from)}`,
        `📝 Reason: ${escapeHtml(reason)}`,
        `🕒 Since: ${fmtTime(Date.now())}`,
      ],
    }),
    { parse_mode: 'HTML' }
  );
});

// ---- /roll ----
bot.command('roll', async (ctx) => {
  const args = getArgsText(ctx).trim();
  const max = parseInt(args, 10) > 0 ? parseInt(args, 10) : 100;
  const result = Math.floor(Math.random() * max) + 1;
  await ctx.reply(
    embed({
      emoji: '🎲',
      title: 'Dice Roll',
      lines: [`👤 ${mention(ctx.from)} rolled...`, `🎯 Result: <b>${result}</b> / ${max}`],
    }),
    { parse_mode: 'HTML' }
  );
});

// ---- /weather ----
bot.command('weather', async (ctx) => {
  const city = getArgsText(ctx).trim();
  if (!city) return ctx.reply('⚠️ Usage: /weather <city name>');
  await ctx.sendChatAction('typing');
  try {
    const w = await fetchWeather(city);
    await ctx.reply(
      embed({
        emoji: '☁️',
        title: `Weather — ${w.location}`,
        lines: [
          `🌡 Temp: <b>${w.tempC}°C</b> (${w.tempF}°F)`,
          `🤔 Feels like: ${w.feelsLikeC}°C (${w.feelsLikeF}°F)`,
          `🌥 Condition: ${escapeHtml(w.desc)}`,
          `📈 Today's range: ${w.todayMinC}°C – ${w.todayMaxC}°C`,
          `💧 Humidity: ${w.humidity}%`,
          `💨 Wind: ${w.windKmph} km/h ${w.windDir}`,
          `🌦 Rain: ${w.precipMm} mm`,
          `☀️ UV Index: ${w.uvIndex}`,
          `👁 Visibility: ${w.visibilityKm} km`,
          ...(w.sunrise ? [`🌅 Sunrise: ${w.sunrise} | 🌇 Sunset: ${w.sunset}`] : []),
        ],
        footer: `Requested by ${displayName(ctx.from)} • data via wttr.in`,
      }),
      { parse_mode: 'HTML' }
    );
  } catch (e) {
    await ctx.reply(`❌ Weather fetch failed: ${e.message}`);
  }
});

// ---- /ai ----
bot.command('ai', async (ctx) => {
  const raw = getArgsText(ctx).trim();

  if (raw.toLowerCase().startsWith('prompt')) {
    const num = raw.match(/1|2|3/);
    if (!num) return ctx.reply('⚠️ Usage: /ai prompt 1  OR  /ai prompt 2  OR  /ai prompt 3');
    await setAiPromptChoice(ctx.from.id, parseInt(num[0], 10));
    return ctx.reply(
      embed({
        emoji: '🎭',
        title: 'AI Persona Updated',
        lines: [`👤 User: ${mention(ctx.from)}`, `🎬 Persona: <b>Prompt ${num[0]}</b>`],
      }),
      { parse_mode: 'HTML' }
    );
  }

  if (!raw) return ctx.reply('⚠️ Usage: /ai <your message>');

  await ctx.sendChatAction('typing');

  // "Thinking..." placeholder, shown while the AI reasons, then removed once the real reply is ready
  let thinkingMsg;
  try {
    thinkingMsg = await ctx.reply('🤔 <i>Thinking...</i>', { parse_mode: 'HTML' });
  } catch (e) {
    console.error('Could not send thinking placeholder:', e.message);
  }

  try {
    const result = await callOpenRouterAI(ctx.from.id, raw);

    if (thinkingMsg) {
      try {
        await ctx.telegram.deleteMessage(ctx.chat.id, thinkingMsg.message_id);
      } catch (e) {
        console.error('Could not delete thinking placeholder:', e.message);
      }
    }

    if (result.type === 'action') {
      // Jarvis-style command execution — locked down to admins, group chats,
      // and the fixed whitelist only.
      if (ctx.chat.type === 'private') {
        return ctx.reply('⚠️ Group management actions sirf group mein chalte hain, DM mein nahi.');
      }
      if (!(await isUserAdmin(ctx, ctx.from.id))) {
        return ctx.reply(
          embed({
            emoji: '🚫',
            title: 'Action Blocked',
            lines: [`Ye action sirf group admins ke liye allowed hai.`],
          }),
          { parse_mode: 'HTML' }
        );
      }
      if (!ALLOWED_AI_ACTIONS.includes(result.action)) {
        return ctx.reply(
          embed({
            emoji: '⚠️',
            title: 'Unknown Action',
            lines: [`AI ne ek aisa action try kiya jo allowed nahi hai: <code>${escapeHtml(result.action || 'unknown')}</code>`],
          }),
          { parse_mode: 'HTML' }
        );
      }
      try {
        const outcome = await executeAiAction(ctx, result.action, result.params);
        return ctx.reply(
          embed({
            emoji: '🤖',
            title: 'AI Executed Action',
            lines: [
              `⚙️ Action: <b>${escapeHtml(result.action)}</b>`,
              `✅ ${escapeHtml(outcome)}`,
              ...(result.reply ? [escapeHtml(result.reply)] : []),
            ],
            footer: `Requested by ${displayName(ctx.from)}`,
          }),
          { parse_mode: 'HTML' }
        );
      } catch (e) {
        return ctx.reply(`❌ Action failed: ${e.message}`);
      }
    }

    // Normal chat reply — already in the user's own language, per the AI action-layer instructions
    await ctx.reply(
      embed({
        emoji: '🤖',
        title: 'AI Reply',
        lines: [escapeHtml(result.reply || '...')],
        footer: `Asked by ${displayName(ctx.from)}`,
      }),
      { parse_mode: 'HTML' }
    );
  } catch (e) {
    if (thinkingMsg) {
      try {
        await ctx.telegram.deleteMessage(ctx.chat.id, thinkingMsg.message_id);
      } catch {}
    }
    await ctx.reply(`❌ AI error: ${e.message}`);
  }
});

// ---- /cinv — create a verified invite link (join request + "not a robot" DM check) ----
bot.command('cinv', async (ctx) => {
  if (!(await requireAdmin(ctx))) return;
  try {
    const link = await ctx.telegram.createChatInviteLink(ctx.chat.id, {
      name: `Verified invite (${new Date().toLocaleDateString('en-IN')})`,
      expire_date: Math.floor(Date.now() / 1000) + 60 * 60, // valid 60 minutes
      creates_join_request: true, // every click needs bot approval, not instant join
    });
    await ctx.reply(
      embed({
        emoji: '🔗',
        title: 'Verified Invite Link Created',
        lines: [
          `🔗 Link: ${link.invite_link}`,
          `⏱ Expires in: <b>60 minutes</b>`,
          `🤖 Anyone who clicks must pass a quick "I'm not a robot" DM check before being added.`,
          `🛡 Created by: ${mention(ctx.from)}`,
        ],
      }),
      { parse_mode: 'HTML' }
    );
  } catch (e) {
    await ctx.reply(`❌ Couldn't create invite link: ${e.message} (bot needs "Invite Users" admin right)`);
  }
});

// ---- chat_join_request: someone clicked the /cinv link ----
bot.on('chat_join_request', async (ctx) => {
  const req = ctx.update.chat_join_request;
  const chatId = req.chat.id;
  const user = req.from;
  const key = `${chatId}:${user.id}`;
  pendingJoins.set(key, { chatId, userId: user.id, chatTitle: req.chat.title || 'the group' });

  try {
    await ctx.telegram.sendMessage(
      user.id,
      embed({
        emoji: '🤖',
        title: 'Quick Verification Required',
        lines: [
          `Aapne <b>${escapeHtml(req.chat.title || 'group')}</b> join karne ke liye request bheji hai.`,
          `Neeche button dabao taaki hum confirm kar sakein ki aap robot nahi ho.`,
        ],
      }),
      {
        parse_mode: 'HTML',
        ...Markup.inlineKeyboard([
          Markup.button.callback('✅ I am not a robot', `verify_human:${chatId}:${user.id}`),
        ]),
      }
    );
  } catch (e) {
    console.error('Could not DM join requester (they may have DMs closed):', e.message);
  }
});

// ---- callback: user clicked "I am not a robot" ----
bot.action(/^verify_human:(-?\d+):(\d+)$/, async (ctx) => {
  const chatId = Number(ctx.match[1]);
  const userId = Number(ctx.match[2]);

  if (ctx.from.id !== userId) {
    return ctx.answerCbQuery('Ye verification aapke liye nahi hai.', { show_alert: true });
  }

  const key = `${chatId}:${userId}`;
  if (!pendingJoins.has(key)) {
    return ctx.answerCbQuery('Ye request expire ho chuki hai ya already verified hai.', { show_alert: true });
  }

  try {
    await ctx.telegram.approveChatJoinRequest(chatId, userId);
    pendingJoins.delete(key);
    await ctx.answerCbQuery('Verified! ✅');
    await ctx.editMessageText(
      embed({
        emoji: '✅',
        title: 'Verified & Added',
        lines: [`Bot ne verify kar liya — aap group mein add ho gaye ho. Welcome! 🎉`],
      }),
      { parse_mode: 'HTML' }
    );
  } catch (e) {
    await ctx.answerCbQuery('Verification failed, try again.', { show_alert: true });
    console.error('approveChatJoinRequest failed:', e.message);
  }
});

// -------------------------------------------------------------------------
// LAUNCH
// -------------------------------------------------------------------------
bot.catch((err, ctx) => {
  console.error(`Unhandled error for update ${ctx.updateType}:`, err);
});

bot.launch().then(() => {
  console.log('✅ Bot is up and running (polling mode)');
});
const express = require('express');
const app = express();
app.get('/health', (req, res) => res.send('OK'));
app.listen(process.env.PORT || 3000);
process.once('SIGINT', () => bot.stop('SIGINT'));
process.once('SIGTERM', () => bot.stop('SIGTERM'));
