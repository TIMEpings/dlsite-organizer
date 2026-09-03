#pragma once

#include <cstddef>
#include <cstdint>
#include <string>
#include <string_view>
#include <vector>

namespace dlsite::shell {

enum class AdmissionStatus {
    Accepted,
    Duplicate,
    QueueFull,
    ShuttingDown,
    Rejected,
    UnsupportedVersion,
};

struct BatchRequest {
    std::string request_id;
    std::vector<std::wstring> paths;
};

struct AdmissionReply {
    std::string request_id;
    AdmissionStatus status = AdmissionStatus::Rejected;
    std::string detail;
    bool has_detail = false;
};

bool GenerateRequestId(std::string& request_id);
bool EncodeBatchRequest(const BatchRequest& request, std::vector<std::uint8_t>& frame);
bool DecodeAdmissionReply(
    const std::vector<std::uint8_t>& frame,
    AdmissionReply& reply,
    std::string& error);

}  // namespace dlsite::shell
