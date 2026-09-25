#pragma once

#include "protocol.h"

#include <string>

namespace dlsite::shell {

enum class PublishResult { Committed, Identical, Conflict, Failed };

class IHandoffPublisher {
public:
    virtual ~IHandoffPublisher() = default;
    virtual PublishResult Publish(const BatchRequest& request) = 0;
    virtual bool MarkLaunchSucceeded(const std::string& request_id) = 0;
    virtual bool MarkLaunchFailed(const std::string& request_id) = 0;
};

class Win32HandoffPublisher final : public IHandoffPublisher {
public:
    explicit Win32HandoffPublisher(std::wstring profile_root);
    PublishResult Publish(const BatchRequest& request) override;
    bool MarkLaunchSucceeded(const std::string& request_id) override;
    bool MarkLaunchFailed(const std::string& request_id) override;

private:
    std::wstring profile_root_;
};

}  // namespace dlsite::shell
