#include "core.hpp"
#define JSMN_STATIC
#define JSMN_STRICT
#include "jsmn.h"

#include <algorithm>
#include <array>
#include <climits>
#include <string_view>

namespace tuner {
namespace {

bool whitespace(char c) {
    return c == ' ' || c == '\t' || c == '\n' || c == '\r';
}

bool name(std::string_view value) {
    if (value.empty() || value.size() > 64) return false;
    for (char c : value) {
        if (!((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
              (c >= '0' && c <= '9') || c == '_' || c == '-')) return false;
    }
    return true;
}

bool integer(std::string_view value, int &out) {
    if (value.empty()) return false;
    const size_t first = value[0] == '-' ? 1 : 0;
    if (first == value.size() || (value[first] == '0' && first + 1 != value.size())) return false;
    int accumulated = 0;
    for (size_t i = first; i < value.size(); ++i) {
        if (value[i] < '0' || value[i] > '9') return false;
        const int digit = value[i] - '0';
        // Reject before multiplication: even a 500-digit input cannot overflow.
        if (accumulated > (INT32_MAX - digit) / 10) return false;
        accumulated = accumulated * 10 + digit;
    }
    out = first ? -accumulated : accumulated;
    return true;
}

enum class Kind { String, Integer, Boolean };

struct Field {
    std::string_view key;
    std::string_view value;
    Kind kind = Kind::String;
    int number = 0;
};

bool known_key(std::string_view key) {
    constexpr std::array<std::string_view, 9> keys = {
        "v", "session", "id", "op", "steps", "rate_hz", "max_duration_ms",
        "profile_hash", "operator_confirmed"
    };
    for (auto candidate : keys) if (key == candidate) return true;
    return false;
}

bool known_operation(std::string_view operation) {
    constexpr std::array<std::string_view, 8> operations = {
        "HELLO", "STATUS", "ARM", "HEARTBEAT", "MOVE", "DISARM", "CLEAR_FAULT", "STOP"
    };
    for (auto candidate : operations) if (operation == candidate) return true;
    return false;
}

} // namespace

bool parse_request(std::string_view text, Request &request, Text &error) {
    error = "MALFORMED";
    request = Request{};
    if (text.size() > 512 || (text.size() == 512 && text.back() != '\n')) {
        error = "FRAME_TOO_LONG";
        return false;
    }
    size_t last = text.size();
    while (last && (text[last - 1] == '\n' || text[last - 1] == '\r')) --last;
    for (size_t i = 0; i < text.size(); ++i) {
        const auto c = static_cast<unsigned char>(text[i]);
        if (c > 127 || c == '\\' || (c < 32 && c != 9 && c != 10 && c != 13)
                || (c == 10 && i < last)) return false;
    }

    jsmn_parser parser;
    std::array<jsmntok_t, 64> tokens{};
    jsmn_init(&parser);
    const int count = jsmn_parse(&parser, text.data(), text.size(), tokens.data(), tokens.size());
    if (count < 1 || tokens[0].type != JSMN_OBJECT || (count - 1) % 2 != 0) return false;
    std::array<Field, 16> fields{};
    const size_t field_count = static_cast<size_t>((count - 1) / 2);
    if (field_count > fields.size()) return false;

    // jsmn supplies bounded tokens. Validate punctuation and the complete flat
    // grammar separately, since tokenization alone does not validate JSON.
    size_t cursor = 0;
    auto skip = [&]() { while (cursor < text.size() && whitespace(text[cursor])) ++cursor; };
    skip();
    if (cursor >= text.size() || text[cursor++] != '{') return false;
    for (size_t index = 0; index < field_count; ++index) {
        skip();
        if (index > 0) {
            if (cursor >= text.size() || text[cursor++] != ',') return false;
            skip();
        }
        const auto &key = tokens[1 + index * 2];
        const auto &value = tokens[2 + index * 2];
        if (key.type != JSMN_STRING || (value.type != JSMN_STRING && value.type != JSMN_PRIMITIVE)) return false;
        if (key.start < 1 || key.end < key.start || value.start < 0 || value.end < value.start
                || static_cast<size_t>(key.end) >= text.size()
                || static_cast<size_t>(value.end) > text.size()) return false;
        if (cursor != static_cast<size_t>(key.start - 1) || text[cursor] != '"') return false;
        auto &field = fields[index];
        field.key = text.substr(key.start, key.end - key.start);
        if (!name(field.key) || !known_key(field.key)) return false;
        for (size_t prior = 0; prior < index; ++prior) {
            if (fields[prior].key == field.key) return false;
        }
        cursor = static_cast<size_t>(key.end + 1);
        skip();
        if (cursor >= text.size() || text[cursor++] != ':') return false;
        skip();
        const size_t expected = static_cast<size_t>(value.type == JSMN_STRING ? value.start - 1 : value.start);
        if (cursor != expected) return false;
        field.value = text.substr(value.start, value.end - value.start);
        if (value.type == JSMN_STRING) {
            if (value.start < 1 || static_cast<size_t>(value.end) >= text.size()) return false;
            field.kind = Kind::String;
            cursor = static_cast<size_t>(value.end + 1);
        } else {
            if (field.value == "true" || field.value == "false") field.kind = Kind::Boolean;
            else {
                field.kind = Kind::Integer;
                if (!integer(field.value, field.number)) return false;
            }
            cursor = static_cast<size_t>(value.end);
        }
    }
    skip();
    if (cursor >= text.size() || text[cursor++] != '}') return false;
    skip();
    if (cursor != text.size()) return false;

    auto find = [&](std::string_view key) -> const Field * {
        for (size_t index = 0; index < field_count; ++index) {
            if (fields[index].key == key) return &fields[index];
        }
        return nullptr;
    };
    const auto *version = find("v");
    const auto *operation = find("op");
    if (!version || version->kind != Kind::Integer || version->number != 1
            || !operation || operation->kind != Kind::String || !known_operation(operation->value)) return false;
    const auto op = operation->value;
    request.op = Text(op);
    if (op == "STOP" && field_count == 2) {
        request.minimal_stop = true;
        return true;
    }
    const size_t expected_count = op == "MOVE" ? 7 : op == "ARM" ? 6 : 4;
    if (field_count != expected_count) return false;
    const auto *session = find("session");
    const auto *id = find("id");
    if (!session || session->kind != Kind::String || session->value.size() != 32
            || !id || id->kind != Kind::Integer || id->number <= 0) return false;
    for (char c : session->value) {
        if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) return false;
    }
    request.session = Text(session->value);
    request.id = id->number;
    if (op == "MOVE") {
        const auto *steps = find("steps");
        const auto *rate = find("rate_hz");
        const auto *duration = find("max_duration_ms");
        if (!steps || steps->kind != Kind::Integer || steps->number == 0
                || !rate || rate->kind != Kind::Integer || rate->number <= 0
                || !duration || duration->kind != Kind::Integer || duration->number <= 0) return false;
        request.steps = steps->number;
        request.rate = rate->number;
        request.duration = duration->number;
    } else if (op == "ARM") {
        const auto *hash = find("profile_hash");
        const auto *confirmed = find("operator_confirmed");
        if (!hash || hash->kind != Kind::String || !name(hash->value)
                || !confirmed || confirmed->kind != Kind::Boolean) return false;
        request.profile_hash = Text(hash->value);
        request.confirmed = confirmed->value == "true";
    }

    // In-place ordering retains exact payload canonicalization without a map,
    // dynamic strings, or allocation. Integer -0 is deliberately normalized.
    std::sort(fields.begin(), fields.begin() + field_count,
              [](const Field &left, const Field &right) { return left.key < right.key; });
    request.canonical.clear();
    for (size_t index = 0; index < field_count; ++index) {
        const auto &field = fields[index];
        request.canonical += Text(field.key);
        request.canonical += "=";
        request.canonical += field.kind == Kind::String ? "s:" : "p:";
        request.canonical += field.kind == Kind::Integer ? decimal(field.number) : Text(field.value);
        request.canonical += ";";
    }
    return true;
}

} // namespace tuner
