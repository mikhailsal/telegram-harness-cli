import type { Language } from "./types.js";

// Replies of /model and /effort. `{name}` marks a value filled by `fill`.
export const settingsCatalog = {
  en: {
    modelCard: "Codex model: {model} · effort {effort}. Pick a model:",
    effortCard: "Reasoning effort for {model}. Pick a level:",
    modelSaved:
      "Model saved: {model} · effort {effort}. Applies to the next turn, queued messages included. Pick an effort:",
    modelHint:
      "Type: /model <id> · /model default (pin Codex's default) · /model reset (stop overriding)",
    effortHint: "Type: /effort <level>",
    otherModels: "Other models: /model <id>",
    defaultLabel: "(default)",
    modelApplied:
      "✓ Model {model} · effort {effort}. Applies from the next turn, including queued messages.",
    effortApplied:
      "✓ Effort {effort} for {model}. Applies from the next turn, including queued messages.",
    effortReset:
      "{model} does not support effort {old}, so the effort is reset to {effort}.",
    keepsCurrentTurn: "The current turn keeps {old}.",
    steerJoins:
      "Steer is on: messages sent during the running turn join it and keep its settings.",
    defaultPinned:
      "Pinned Codex's default model {model}. A later change of Codex's default is not followed until you run /model default again.",
    modelReset:
      "✓ Telegram override removed. The thread keeps {current} until it is changed in the TUI or /new starts from Codex's config.",
    currentSetting: "the thread's current setting",
    effortDefault: "default",
    settingsGone:
      "{id} is no longer offered. Nothing changed; the card is refreshed.",
    settingsChanged:
      "The setting changed meanwhile. Nothing changed by this tap; the card is refreshed.",
    settingsChangedTyped:
      "The setting changed while this command waited. Nothing changed by it; send it again if you still want it.",
    noModels:
      "⚠️ Codex reported no selectable models. Nothing changed. Details: thc doctor",
    noEfforts:
      "{model} has no reasoning effort levels to choose. Nothing changed.",
    settingsUnavailable:
      "⚠️ Could not read the Codex model list ({reason}). Nothing changed. Try again; if it repeats: thc doctor",
    settingsUnsupported:
      "⚠️ This Codex cannot list models; /model needs codex-cli 0.169.0 or newer. Nothing changed. Check the installed version: thc doctor",
    settingsNeedProtocol:
      'Model switching needs the protocol adapter; this bridge uses tmux. Nothing changed. See docs/cli.md, "Choosing tmux".',
    settingsNotReady:
      "Codex is not connected yet. Nothing changed. Try again in a moment; /status shows the connection.",
    unknownModel: '"{input}" is not a model here. Choose one of: ',
    unknownEffort: '"{input}" is not supported by {model}. Choose one of: ',
    modelUnavailable:
      "⚠️ {model} is not available in this Codex. Your last message was not processed. Queued messages wait until you switch. Switch to {default}, then send it again.",
    modelUnavailableManual:
      "⚠️ {model} is not available in this Codex. Your last message was not processed. Queued messages wait until you switch. Pick another model with /model, then send it again.",
    recoverButton: "Switch to {default}",
    statusRunning: "Running: {model} · effort {effort}",
    statusRunningUnknown: "Running: unknown",
    statusNext: "Next turns: {settings} (Telegram override)",
    statusNoOverride: "Next turns: no Telegram override",
    statusPending: "pending: applies from the next turn",
    statusDiffers:
      "differs from the running thread (changed in the TUI?); the next Telegram turn applies the override",
  },
  ru: {
    modelCard: "Модель Codex: {model} · глубина {effort}. Выбери модель:",
    effortCard: "Глубина рассуждений для {model}. Выбери уровень:",
    modelSaved:
      "Модель сохранена: {model} · глубина {effort}. Действует со следующей задачи, включая сообщения в очереди. Выбери глубину:",
    modelHint:
      "Текстом: /model <id> · /model default (закрепить модель Codex по умолчанию) · /model reset (больше не переопределять)",
    effortHint: "Текстом: /effort <уровень>",
    otherModels: "Другие модели: /model <id>",
    defaultLabel: "(по умолчанию)",
    modelApplied:
      "✓ Модель {model} · глубина {effort}. Действует со следующей задачи, включая сообщения в очереди.",
    effortApplied:
      "✓ Глубина {effort} для {model}. Действует со следующей задачи, включая сообщения в очереди.",
    effortReset:
      "{model} не поддерживает глубину {old}, поэтому глубина сброшена на {effort}.",
    keepsCurrentTurn: "Текущая задача продолжает с {old}.",
    steerJoins:
      "Включён Steer: сообщения во время задачи присоединяются к ней и сохраняют её настройки.",
    defaultPinned:
      "Закреплена модель Codex по умолчанию {model}. Если Codex сменит модель по умолчанию, мост не последует за ней, пока снова не выполнишь /model default.",
    modelReset:
      "✓ Переопределение из Telegram снято. Разговор сохраняет {current}, пока её не сменят в TUI или /new не начнёт разговор по конфигурации Codex.",
    currentSetting: "текущую настройку разговора",
    effortDefault: "по умолчанию",
    settingsGone:
      "{id} больше не предлагается. Ничего не изменилось; карточка обновлена.",
    settingsChanged:
      "Настройка успела измениться. Это нажатие ничего не изменило; карточка обновлена.",
    settingsChangedTyped:
      "Настройка изменилась, пока команда ждала. Эта команда ничего не изменила; отправь её снова, если она ещё нужна.",
    noModels:
      "⚠️ Codex не сообщил ни одной доступной модели. Ничего не изменилось. Подробности: thc doctor",
    noEfforts:
      "У {model} нет уровней глубины рассуждений. Ничего не изменилось.",
    settingsUnavailable:
      "⚠️ Не удалось прочитать список моделей Codex ({reason}). Ничего не изменилось. Попробуй ещё раз; если повторится: thc doctor",
    settingsUnsupported:
      "⚠️ Этот Codex не умеет перечислять модели; для /model нужен codex-cli 0.169.0 или новее. Ничего не изменилось. Проверь установленную версию: thc doctor",
    settingsNeedProtocol:
      'Смена модели работает только с протокольным адаптером, а этот мост использует tmux. Ничего не изменилось. См. docs/cli.md, раздел "Choosing tmux".',
    settingsNotReady:
      "Codex ещё не подключён. Ничего не изменилось. Попробуй чуть позже; подключение видно в /status.",
    unknownModel: '"{input}" — здесь нет такой модели. Выбери одну из: ',
    unknownEffort:
      '"{input}" не поддерживается моделью {model}. Выбери один из: ',
    modelUnavailable:
      "⚠️ Модель {model} недоступна в этом Codex. Последнее сообщение не обработано. Сообщения в очереди ждут, пока не сменишь модель. Переключись на {default} и отправь его снова.",
    modelUnavailableManual:
      "⚠️ Модель {model} недоступна в этом Codex. Последнее сообщение не обработано. Сообщения в очереди ждут, пока не сменишь модель. Выбери другую модель через /model и отправь его снова.",
    recoverButton: "Переключиться на {default}",
    statusRunning: "Сейчас: {model} · глубина {effort}",
    statusRunningUnknown: "Сейчас: неизвестно",
    statusNext: "Следующие задачи: {settings} (переопределено из Telegram)",
    statusNoOverride: "Следующие задачи: без переопределения из Telegram",
    statusPending: "ожидает: действует со следующей задачи",
    statusDiffers:
      "отличается от текущего разговора (изменено в TUI?); следующая задача из Telegram применит переопределение",
  },
} satisfies Record<Language, Record<string, string>>;
type Assert<T extends true> = T;
export type SettingsCatalogParity = Assert<
  keyof typeof settingsCatalog.en extends keyof typeof settingsCatalog.ru
    ? keyof typeof settingsCatalog.ru extends keyof typeof settingsCatalog.en
      ? true
      : false
    : false
>;
export type SettingsText = keyof typeof settingsCatalog.en;

export function fill(
  language: Language,
  key: SettingsText,
  values: Record<string, string> = {},
): string {
  return settingsCatalog[language][key].replace(
    /\{(\w+)\}/gu,
    (match, name: string) => values[name] ?? match,
  );
}
