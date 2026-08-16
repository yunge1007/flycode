from pathlib import Path
import re


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


# -----------------------------------------------------------------------------
# 1. Session loading: keep requests small and avoid refresh loops.
# -----------------------------------------------------------------------------
session_provider = Path('lib/providers/session_provider.dart')
s = session_provider.read_text()
pattern = re.compile(
    r'(final messages = await api\.getSessionMessages\(\n\s+sessionID,\n\s+directory: directory,)(\n\s+\);)'
)
s, count = pattern.subn(r'\1\n      limit: 20,\2', s)
require(count == 2, f'Expected two session message fetches, patched {count}')
session_provider.write_text(s)

handlers = Path('lib/providers/global_event/handlers.dart')
hs = handlers.read_text()
old_handler = re.compile(
    r"    if \(payload is! EventSessionCreated &&\n"
    r"        payload is! EventSessionUpdated &&\n"
    r"        payload is! EventSessionDeleted\) \{\n"
    r"      return;\n"
    r"    \}\n\n"
    r"    ref\.invalidate\(sessionsProvider\);\n"
    r"    ref\.invalidate\(allSessionsProvider\);\n"
)
new_handler = (
    "    // session.updated is emitted frequently while a conversation is active.\n"
    "    // Invalidating the root session list for every update can keep the chat\n"
    "    // page in a perpetual loading/refresh cycle on slower mobile clients.\n"
    "    if (payload is EventSessionUpdated) {\n"
    "      return;\n"
    "    }\n"
    "    if (payload is! EventSessionCreated && payload is! EventSessionDeleted) {\n"
    "      return;\n"
    "    }\n\n"
    "    ref.invalidate(sessionsProvider);\n"
    "    ref.invalidate(allSessionsProvider);\n"
)
hs, count = old_handler.subn(new_handler, hs, count=1)
require(count == 1, f'Expected one session invalidation block, patched {count}')
handlers.write_text(hs)

# -----------------------------------------------------------------------------
# 2. Network timeout: message history can be slow over a remote VPS/mobile link.
# -----------------------------------------------------------------------------
api_client = Path('lib/service/api/api_client.dart')
a = api_client.read_text()
old_timeout = 'static const Duration _defaultRequestTimeout = Duration(seconds: 15);'
new_timeout = 'static const Duration _defaultRequestTimeout = Duration(seconds: 60);'
require(old_timeout in a, 'Could not find ApiClient 15 second timeout')
api_client.write_text(a.replace(old_timeout, new_timeout, 1))

# -----------------------------------------------------------------------------
# 3. Message compatibility: OpenCode adds new Part types over time and old
#    sessions can also contain partially populated rows. Do not fail the entire
#    conversation because one message/part cannot be decoded by FlyCode 1.1.0.
# -----------------------------------------------------------------------------
parts = Path('lib/service/api/models/parts.dart')
p = parts.read_text()
unknown_part_class = r'''
class UnknownPart {
  final String id;
  final String sessionID;
  final String messageID;
  final String type;
  final Map<String, dynamic> raw;

  UnknownPart({
    required this.id,
    required this.sessionID,
    required this.messageID,
    required this.type,
    required this.raw,
  });

  factory UnknownPart.fromJson(Map<String, dynamic> json) => UnknownPart(
    id: json['id']?.toString() ?? '',
    sessionID: json['sessionID']?.toString() ?? '',
    messageID: json['messageID']?.toString() ?? '',
    type: json['type']?.toString() ?? 'unknown',
    raw: Map<String, dynamic>.from(json),
  );
}

'''
marker = 'String? partId(Object part) {'
require(marker in p, 'Could not find partId marker')
if 'class UnknownPart {' not in p:
    p = p.replace(marker, unknown_part_class + marker, 1)
parts.write_text(p)

message = Path('lib/service/api/models/message.dart')
m = message.read_text()

parse_msg_start = m.index('Object parseMsg(Map<String, dynamic> json) {')
parse_msg_end_marker = '@JsonSerializable(createFactory: false, createToJson: false)\nclass MessageWithParts'
parse_msg_end = m.index(parse_msg_end_marker, parse_msg_start)
parse_msg_replacement = r'''Object parseMsg(Map<String, dynamic> json) {
  final role = json['role']?.toString() ?? 'assistant';
  try {
    if (role == 'user') {
      return UserMessage.fromJson(json);
    }
    if (role == 'assistant') {
      return AssistantMessage.fromJson(json);
    }
  } catch (_) {
    // Fall through to a tolerant decoder below. Historical OpenCode sessions
    // can be missing fields that are mandatory in newer schemas.
  }

  final timeJson = json['time'] is Map
      ? Map<String, dynamic>.from(json['time'] as Map)
      : <String, dynamic>{};
  final time = MessageTime(
    created: (timeJson['created'] as num?)?.toInt() ?? 0,
    completed: (timeJson['completed'] as num?)?.toInt(),
  );

  if (role == 'user') {
    final modelJson = json['model'] is Map
        ? Map<String, dynamic>.from(json['model'] as Map)
        : <String, dynamic>{};
    return UserMessage(
      id: json['id']?.toString() ?? '',
      sessionID: json['sessionID']?.toString() ?? '',
      role: 'user',
      time: time,
      agent: json['agent']?.toString() ?? '',
      model: MessageModel(
        providerID: modelJson['providerID']?.toString() ?? json['providerID']?.toString() ?? '',
        modelID: modelJson['modelID']?.toString() ?? json['modelID']?.toString() ?? '',
      ),
      variant: modelJson['variant']?.toString() ?? json['variant']?.toString(),
      system: json['system']?.toString(),
      tools: null,
    );
  }

  final pathJson = json['path'] is Map
      ? Map<String, dynamic>.from(json['path'] as Map)
      : <String, dynamic>{};
  final tokensJson = json['tokens'] is Map
      ? Map<String, dynamic>.from(json['tokens'] as Map)
      : <String, dynamic>{};
  final cacheJson = tokensJson['cache'] is Map
      ? Map<String, dynamic>.from(tokensJson['cache'] as Map)
      : <String, dynamic>{};

  return AssistantMessage(
    id: json['id']?.toString() ?? '',
    sessionID: json['sessionID']?.toString() ?? '',
    role: 'assistant',
    time: time,
    error: null,
    parentID: json['parentID']?.toString() ?? '',
    modelID: json['modelID']?.toString() ?? '',
    providerID: json['providerID']?.toString() ?? '',
    agent: json['agent']?.toString(),
    variant: json['variant']?.toString(),
    mode: json['mode']?.toString() ?? json['agent']?.toString() ?? 'assistant',
    path: MessagePath(
      cwd: pathJson['cwd']?.toString() ?? '',
      root: pathJson['root']?.toString() ?? '',
    ),
    summary: json['summary'] is bool ? json['summary'] as bool : null,
    cost: (json['cost'] as num?)?.toDouble(),
    tokens: MessageTokens(
      input: (tokensJson['input'] as num?)?.toInt(),
      output: (tokensJson['output'] as num?)?.toInt(),
      total: (tokensJson['total'] as num?)?.toInt(),
      reasoning: (tokensJson['reasoning'] as num?)?.toInt(),
      cache: MessageCacheTokens(
        read: (cacheJson['read'] as num?)?.toInt(),
        write: (cacheJson['write'] as num?)?.toInt(),
      ),
    ),
    finish: json['finish']?.toString(),
  );
}

'''
m = m[:parse_msg_start] + parse_msg_replacement + m[parse_msg_end:]

parse_part_start = m.index('Object parsePart(Map<String, dynamic> json) {')
parse_part_end_marker = 'Map<String, dynamic> partToJson(Object part) {'
parse_part_end = m.index(parse_part_end_marker, parse_part_start)
parse_part_replacement = r'''Object parsePart(Map<String, dynamic> json) {
  final type = json['type']?.toString() ?? 'unknown';
  try {
    switch (type) {
      case 'text':
        return TextPart.fromJson(json);
      case 'tool':
        return ToolPart.fromJson(json);
      case 'reasoning':
        return ReasoningPart.fromJson(json);
      case 'file':
        return FilePart.fromJson(json);
      case 'step-start':
        return StepStartPart.fromJson(json);
      case 'step-finish':
        return StepFinishPart.fromJson(json);
      case 'snapshot':
        return SnapshotPart.fromJson(json);
      case 'patch':
        return PatchPart.fromJson(json);
      case 'agent':
        return AgentPart.fromJson(json);
      case 'retry':
        return RetryPart.fromJson(json);
      case 'compaction':
        return CompactionPart.fromJson(json);
      case 'subtask':
        return SubtaskPart.fromJson(json);
      default:
        return UnknownPart.fromJson(json);
    }
  } catch (_) {
    // A malformed legacy part or a newer OpenCode part must not prevent the
    // rest of the conversation from being displayed.
    return UnknownPart.fromJson(json);
  }
}

'''
m = m[:parse_part_start] + parse_part_replacement + m[parse_part_end:]
m = m.replace(
    'Map<String, dynamic> partToJson(Object part) {\n',
    'Map<String, dynamic> partToJson(Object part) {\n  if (part is UnknownPart) return part.raw;\n',
    1,
)
message.write_text(m)

# -----------------------------------------------------------------------------
# 4. Diagnostic error text. If the server itself rejects a legacy session with
#    HTTP 400, expose the actual ApiException rather than a generic message.
# -----------------------------------------------------------------------------
message_list = Path('lib/widgets/message/message_list.dart')
ml = message_list.read_text()
old = 'return MessageErrorState(message: l10n.messageListLoadFailed);'
new = "return MessageErrorState(message: '${l10n.messageListLoadFailed}\\n$error');"
require(old in ml, 'Could not find MessageList error state')
message_list.write_text(ml.replace(old, new, 1))

sub_session = Path('lib/pages/sub_session_page.dart')
ss = sub_session.read_text()
require(old in ss, 'Could not find SubSession error state')
sub_session.write_text(ss.replace(old, new, 1))

print('Applied HarmonyOS/OpenCode chat compatibility patches:')
print(' - 20 message initial history')
print(' - 60 second HTTP timeout')
print(' - session.updated refresh-loop guard')
print(' - tolerant legacy/new message decoding')
print(' - visible detailed load errors')
