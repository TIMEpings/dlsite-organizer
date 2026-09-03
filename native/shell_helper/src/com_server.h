#pragma once

#include "ipc_client.h"
#include "selection_adapter.h"

#include <windows.h>

namespace dlsite::shell {

HRESULT RunComLocalServer(ISelectionAdapter& selection_adapter, IBatchDispatcher& dispatcher);

}  // namespace dlsite::shell
