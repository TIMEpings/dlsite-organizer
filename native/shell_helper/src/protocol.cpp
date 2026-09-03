#include "protocol.h"

#include "constants.h"
#include "utf.h"

#include <windows.h>
#include <objbase.h>

#include <algorithm>
#include <array>
#include <cctype>
#include <limits>
#include <string>
#include <string_view>

namespace dlsite::shell {
namespace {

bool IsRequestId(std::string_view value) {
    if (value.empty() || value.size() > kMaxRequestIdLength) {
        return false;
    }
    return std::all_of(value.begin(), value.end(), [](unsigned char character) {
        return (character >= 'A' && character <= 'Z') ||
               (character >= 'a' && character <= 'z') ||
               (character >= '0' && character <= '9') ||
               character == '_' || character == '-';
    });
}

bool IsSafePath(const std::wstring& path) {
    if (path.empty() || path.size() > kMaxQuickRenamePathLength) {
        return false;
    }
    return std::all_of(path.begin(), path.end(), [](wchar_t character) {
        return character >= 0x20 && character != 0x7f;
    });
}

void AppendJsonString(std::string_view value, std::string& json) {
    static constexpr char hex[] = "0123456789abcdef";
    json.push_back('"');
    for (const unsigned char character : value) {
        switch (character) {
        case '"':
            json += "\\\"";
            break;
        case '\\':
            json += "\\\\";
            break;
        case '\b':
            json += "\\b";
            break;
        case '\f':
            json += "\\f";
            break;
        case '\n':
            json += "\\n";
            break;
        case '\r':
            json += "\\r";
            break;
        case '\t':
            json += "\\t";
            break;
        default:
            if (character < 0x20) {
                json += "\\u00";
                json.push_back(hex[(character >> 4) & 0x0f]);
                json.push_back(hex[character & 0x0f]);
            } else {
                json.push_back(static_cast<char>(character));
            }
            break;
        }
    }
    json.push_back('"');
}

bool BuildRequestJson(const BatchRequest& request, std::string& json) {
    if (!IsRequestId(request.request_id) || request.paths.empty() ||
        request.paths.size() > kMaxQuickRenameItems) {
        return false;
    }

    json = "{\"version\":1,\"request_id\":";
    AppendJsonString(request.request_id, json);
    json += ",\"command\":\"QUICK_RENAME\",\"payload\":{\"paths\":[";
    for (std::size_t index = 0; index < request.paths.size(); ++index) {
        if (!IsSafePath(request.paths[index])) {
            return false;
        }
        std::string path_utf8;
        if (!WideToUtf8(request.paths[index], path_utf8)) {
            return false;
        }
        if (index != 0) {
            json.push_back(',');
        }
        AppendJsonString(path_utf8, json);
        if (json.size() > kMaxQuickRenameRequestPayloadSize) {
            return false;
        }
    }
    json += "]}}";
    return json.size() <= kMaxQuickRenameRequestPayloadSize;
}

bool AddFrameHeader(std::string_view payload, std::vector<std::uint8_t>& frame) {
    if (payload.size() > kMaxRequestFrameSize - kFrameHeaderSize ||
        payload.size() > std::numeric_limits<std::uint32_t>::max()) {
        return false;
    }
    const auto length = static_cast<std::uint32_t>(payload.size());
    frame.resize(kFrameHeaderSize + payload.size());
    frame[0] = static_cast<std::uint8_t>((length >> 24) & 0xff);
    frame[1] = static_cast<std::uint8_t>((length >> 16) & 0xff);
    frame[2] = static_cast<std::uint8_t>((length >> 8) & 0xff);
    frame[3] = static_cast<std::uint8_t>(length & 0xff);
    std::copy(payload.begin(), payload.end(), frame.begin() + kFrameHeaderSize);
    return true;
}

class JsonReader final {
public:
    explicit JsonReader(std::string_view value) : value_(value) {}

    bool ReadReply(AdmissionReply& reply, std::string& error) {
        SkipWhitespace();
        if (!Consume('{')) {
            return Fail(error, "reply is not an object");
        }
        bool have_version = false;
        bool have_request_id = false;
        bool have_status = false;
        bool have_detail = false;
        bool first = true;
        while (true) {
            SkipWhitespace();
            if (Consume('}')) {
                break;
            }
            if (!first && !Consume(',')) {
                return Fail(error, "reply object separator is invalid");
            }
            if (!first) {
                SkipWhitespace();
                if (position_ < value_.size() && value_[position_] == '}') {
                    return Fail(error, "trailing reply separator");
                }
            }
            first = false;

            std::string key;
            if (!ReadString(key) || key.empty()) {
                return Fail(error, "reply key is invalid");
            }
            if (!Consume(':')) {
                return Fail(error, "reply key separator is invalid");
            }
            if (key == "version") {
                if (have_version || !ReadVersion(reply, error)) {
                    return have_version ? Fail(error, "duplicate reply version") : false;
                }
                have_version = true;
            } else if (key == "request_id") {
                if (have_request_id || !ReadString(reply.request_id)) {
                    return have_request_id ? Fail(error, "duplicate reply request id")
                                            : Fail(error, "reply request id is invalid");
                }
                have_request_id = true;
            } else if (key == "status") {
                if (have_status || !ReadStatus(reply.status, error)) {
                    return have_status ? Fail(error, "duplicate reply status") : false;
                }
                have_status = true;
            } else if (key == "detail") {
                if (have_detail || !ReadString(reply.detail)) {
                    return have_detail ? Fail(error, "duplicate reply detail")
                                       : Fail(error, "reply detail is invalid");
                }
                std::wstring detail_wide;
                if (!Utf8ToWide(reply.detail, detail_wide) ||
                    detail_wide.size() > kMaxReplyDetailLength ||
                    std::any_of(detail_wide.begin(), detail_wide.end(), [](wchar_t character) {
                        return character < 0x20 || character == 0x7f;
                    })) {
                    return Fail(error, "reply detail is too long");
                }
                have_detail = true;
            } else {
                return Fail(error, "unknown reply field");
            }
        }
        SkipWhitespace();
        if (position_ != value_.size()) {
            return Fail(error, "trailing reply bytes");
        }
        if (!have_version || !have_request_id || !have_status || !IsRequestId(reply.request_id)) {
            return Fail(error, "reply is missing required fields");
        }
        return true;
    }

private:
    bool ReadVersion(AdmissionReply&, std::string& error) {
        const std::size_t start = position_;
        if (position_ >= value_.size() || value_[position_] != '1') {
            return Fail(error, "unsupported reply version");
        }
        ++position_;
        if (position_ < value_.size() &&
            (std::isdigit(static_cast<unsigned char>(value_[position_])) != 0 ||
             value_[position_] == '.' || value_[position_] == 'e' || value_[position_] == 'E')) {
            position_ = start;
            return Fail(error, "reply version is not an integer");
        }
        return true;
    }

    bool ReadStatus(AdmissionStatus& status, std::string& error) {
        std::string value;
        if (!ReadString(value)) {
            return Fail(error, "reply status is invalid");
        }
        if (value == "ACCEPTED") {
            status = AdmissionStatus::Accepted;
        } else if (value == "DUPLICATE") {
            status = AdmissionStatus::Duplicate;
        } else if (value == "QUEUE_FULL") {
            status = AdmissionStatus::QueueFull;
        } else if (value == "SHUTTING_DOWN") {
            status = AdmissionStatus::ShuttingDown;
        } else if (value == "REJECTED") {
            status = AdmissionStatus::Rejected;
        } else if (value == "UNSUPPORTED_VERSION") {
            status = AdmissionStatus::UnsupportedVersion;
        } else {
            return Fail(error, "unknown reply status");
        }
        return true;
    }

    bool ReadString(std::string& output) {
        output.clear();
        if (!Consume('"')) {
            return false;
        }
        while (position_ < value_.size()) {
            const unsigned char character = static_cast<unsigned char>(value_[position_++]);
            if (character == '"') {
                return IsValidUtf8(output);
            }
            if (character < 0x20) {
                return false;
            }
            if (character != '\\') {
                output.push_back(static_cast<char>(character));
                continue;
            }
            if (position_ >= value_.size()) {
                return false;
            }
            const char escaped = value_[position_++];
            switch (escaped) {
            case '"':
            case '\\':
            case '/':
                output.push_back(escaped);
                break;
            case 'b':
                output.push_back('\b');
                break;
            case 'f':
                output.push_back('\f');
                break;
            case 'n':
                output.push_back('\n');
                break;
            case 'r':
                output.push_back('\r');
                break;
            case 't':
                output.push_back('\t');
                break;
            case 'u':
                if (!ReadUnicodeEscape(output)) {
                    return false;
                }
                break;
            default:
                return false;
            }
        }
        return false;
    }

    bool ReadUnicodeEscape(std::string& output) {
        std::uint32_t codepoint = 0;
        if (!ReadHexQuad(codepoint)) {
            return false;
        }
        if (codepoint >= 0xd800 && codepoint <= 0xdbff) {
            if (position_ + 6 > value_.size() || value_[position_] != '\\' || value_[position_ + 1] != 'u') {
                return false;
            }
            position_ += 2;
            std::uint32_t low = 0;
            if (!ReadHexQuad(low) || low < 0xdc00 || low > 0xdfff) {
                return false;
            }
            codepoint = 0x10000 + ((codepoint - 0xd800) << 10) + (low - 0xdc00);
        } else if (codepoint >= 0xdc00 && codepoint <= 0xdfff) {
            return false;
        }
        AppendUtf8(codepoint, output);
        return true;
    }

    bool ReadHexQuad(std::uint32_t& value) {
        if (position_ + 4 > value_.size()) {
            return false;
        }
        value = 0;
        for (int count = 0; count != 4; ++count) {
            const char character = value_[position_++];
            value <<= 4;
            if (character >= '0' && character <= '9') {
                value += static_cast<std::uint32_t>(character - '0');
            } else if (character >= 'a' && character <= 'f') {
                value += static_cast<std::uint32_t>(character - 'a' + 10);
            } else if (character >= 'A' && character <= 'F') {
                value += static_cast<std::uint32_t>(character - 'A' + 10);
            } else {
                return false;
            }
        }
        return true;
    }

    static void AppendUtf8(std::uint32_t codepoint, std::string& output) {
        if (codepoint <= 0x7f) {
            output.push_back(static_cast<char>(codepoint));
        } else if (codepoint <= 0x7ff) {
            output.push_back(static_cast<char>(0xc0 | (codepoint >> 6)));
            output.push_back(static_cast<char>(0x80 | (codepoint & 0x3f)));
        } else if (codepoint <= 0xffff) {
            output.push_back(static_cast<char>(0xe0 | (codepoint >> 12)));
            output.push_back(static_cast<char>(0x80 | ((codepoint >> 6) & 0x3f)));
            output.push_back(static_cast<char>(0x80 | (codepoint & 0x3f)));
        } else {
            output.push_back(static_cast<char>(0xf0 | (codepoint >> 18)));
            output.push_back(static_cast<char>(0x80 | ((codepoint >> 12) & 0x3f)));
            output.push_back(static_cast<char>(0x80 | ((codepoint >> 6) & 0x3f)));
            output.push_back(static_cast<char>(0x80 | (codepoint & 0x3f)));
        }
    }

    static bool IsValidUtf8(const std::string& value) {
        std::wstring wide;
        return Utf8ToWide(value, wide);
    }

    bool Consume(char expected) {
        if (position_ >= value_.size() || value_[position_] != expected) {
            return false;
        }
        ++position_;
        return true;
    }

    void SkipWhitespace() {
        while (position_ < value_.size()) {
            const char character = value_[position_];
            if (character != ' ' && character != '\t' && character != '\r' && character != '\n') {
                break;
            }
            ++position_;
        }
    }

    static bool Fail(std::string& error, const char* message) {
        error = message;
        return false;
    }

    std::string_view value_;
    std::size_t position_ = 0;
};

}  // namespace

bool GenerateRequestId(std::string& request_id) {
    GUID guid{};
    if (FAILED(CoCreateGuid(&guid))) {
        return false;
    }
    wchar_t buffer[40]{};
    const int length = StringFromGUID2(guid, buffer, static_cast<int>(std::size(buffer)));
    if (length <= 2 || buffer[0] != L'{' || buffer[length - 2] != L'}') {
        return false;
    }
    std::wstring_view without_braces(buffer + 1, static_cast<std::size_t>(length - 3));
    return WideToUtf8(without_braces, request_id) && IsRequestId(request_id);
}

bool EncodeBatchRequest(const BatchRequest& request, std::vector<std::uint8_t>& frame) {
    std::string json;
    if (!BuildRequestJson(request, json)) {
        return false;
    }
    return AddFrameHeader(json, frame);
}

bool DecodeAdmissionReply(
    const std::vector<std::uint8_t>& frame,
    AdmissionReply& reply,
    std::string& error) {
    error.clear();
    if (frame.size() < kFrameHeaderSize) {
        error = "reply frame header is truncated";
        return false;
    }
    const std::uint32_t length =
        (static_cast<std::uint32_t>(frame[0]) << 24) |
        (static_cast<std::uint32_t>(frame[1]) << 16) |
        (static_cast<std::uint32_t>(frame[2]) << 8) |
        static_cast<std::uint32_t>(frame[3]);
    if (length > kMaxResponseFrameSize - kFrameHeaderSize ||
        frame.size() != kFrameHeaderSize + static_cast<std::size_t>(length)) {
        error = "reply frame size is invalid";
        return false;
    }
    JsonReader reader(std::string_view(
        reinterpret_cast<const char*>(frame.data() + kFrameHeaderSize), length));
    return reader.ReadReply(reply, error);
}

}  // namespace dlsite::shell
