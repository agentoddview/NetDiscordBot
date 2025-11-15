-- ServerScriptService/DiscordPresence.server.lua

local Players = game:GetService("Players")
local HttpService = game:GetService("HttpService")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local ACTIVITY_EVENT_NAME = "PlayerActivity"
local activityEvent = ReplicatedStorage:FindFirstChild(ACTIVITY_EVENT_NAME)
if not activityEvent then
	activityEvent = Instance.new("RemoteEvent")
	activityEvent.Name = ACTIVITY_EVENT_NAME
	activityEvent.Parent = ReplicatedStorage
end

---------------------------------------------------------------------
-- CONFIG
---------------------------------------------------------------------

-- Your Discord bot's public URL (Coolify):
local PRESENCE_ENDPOINT = "http://71.212.82.1.sslip.io:3000/roblox/presence"

-- MUST match ROBLOX_GAME_SECRET in your Discord bot .env
local GAME_SECRET = "wetrust"

-- AFK timeout in seconds (10 minutes)
local INACTIVITY_SECONDS = 10 * 60

-- Only track staff in this group / rank
local STAFF_GROUP_ID = 13039250
local MIN_STAFF_RANK = 121

---------------------------------------------------------------------
-- Internal state
---------------------------------------------------------------------
local lastActivity = {}    -- [player] = os.time()
local inactiveSent = {}    -- [player] = bool
local isTrackedStaff = {}  -- [player] = bool (cached)

---------------------------------------------------------------------
-- Helper: check if player is staff we care about
---------------------------------------------------------------------
local function playerIsTrackedStaff(player: Player): boolean
	-- cache result so we don't call GetRankInGroup constantly
	if isTrackedStaff[player] ~= nil then
		return isTrackedStaff[player]
	end

	local ok, rankOrErr = pcall(function()
		return player:GetRankInGroup(STAFF_GROUP_ID)
	end)

	if not ok then
		warn("[DiscordPresence] GetRankInGroup failed for", player.Name, player.UserId, rankOrErr)
		isTrackedStaff[player] = false
		return false
	end

	local rank = rankOrErr :: number
	local tracked = rank >= MIN_STAFF_RANK
	isTrackedStaff[player] = tracked

	if tracked then
		print(("[DiscordPresence] %s (rank %d) is staff, tracking presence")
			:format(player.Name, rank))
	else
		print(("[DiscordPresence] %s (rank %d) is not staff, ignoring presence")
			:format(player.Name, rank))
	end

	return tracked
end

---------------------------------------------------------------------
-- Helper: send presence event to Discord bot (staff only)
---------------------------------------------------------------------
local function sendPresenceEvent(player: Player, eventName: string)
	if not playerIsTrackedStaff(player) then
		-- Not staff → don't spam the bot / Bloxlink
		return
	end

	local payloadTable = {
		roblox_id = tostring(player.UserId),
		event = eventName,
	}

	local success, jsonOrErr = pcall(function()
		return HttpService:JSONEncode(payloadTable)
	end)

	if not success then
		warn("[DiscordPresence] JSON encode failed:", jsonOrErr)
		return
	end

	local requestOptions = {
		Url = PRESENCE_ENDPOINT,
		Method = "POST",
		Headers = {
			["Content-Type"] = "application/json",
			["X-Game-Secret"] = GAME_SECRET,
		},
		Body = jsonOrErr,
	}

	print(("[DiscordPresence] Sending %s → %s"):format(eventName, PRESENCE_ENDPOINT))

	local ok, result = pcall(function()
		return HttpService:RequestAsync(requestOptions)
	end)

	if not ok then
		warn("[DiscordPresence] HTTP request error:", result)
		return
	end

	if result.Success then
		print(("[DiscordPresence] HTTP %s OK (%d)"):format(eventName, result.StatusCode))
	else
		warn(("[DiscordPresence] HTTP %s FAILED: %d %s")
			:format(eventName, result.StatusCode, result.Body))
	end
end

---------------------------------------------------------------------
-- Activity / AFK tracking (staff only)
---------------------------------------------------------------------
local function markActivity(player: Player)
	if not playerIsTrackedStaff(player) then
		return
	end
	lastActivity[player] = os.time()
	inactiveSent[player] = false
end

local function startAfkLoop(player: Player)
	if not playerIsTrackedStaff(player) then
		return
	end

	task.spawn(function()
		while player.Parent do
			task.wait(30)

			local last = lastActivity[player]
			if not last then
				markActivity(player)
				continue
			end

			local diff = os.time() - last
			if diff >= INACTIVITY_SECONDS and not inactiveSent[player] then
				inactiveSent[player] = true
				print(("[DiscordPresence] %s inactive for %d seconds, sending 'inactive'")
					:format(player.Name, diff))
				sendPresenceEvent(player, "inactive")
			end
		end
	end)
end

---------------------------------------------------------------------
-- Connections
---------------------------------------------------------------------
Players.PlayerAdded:Connect(function(player)
	print("[DiscordPresence] PlayerJoined:", player.Name, player.UserId)

	-- Only track if staff
	if not playerIsTrackedStaff(player) then
		return
	end

	markActivity(player)
	startAfkLoop(player)

	-- Tell Discord "I'm in the game now"
	sendPresenceEvent(player, "join")
end)

Players.PlayerRemoving:Connect(function(player)
	print("[DiscordPresence] PlayerLeaving:", player.Name, player.UserId)

	if playerIsTrackedStaff(player) then
		-- Tell Discord "I left the game"
		sendPresenceEvent(player, "leave")
	end

	lastActivity[player] = nil
	inactiveSent[player] = nil
	isTrackedStaff[player] = nil
end)

activityEvent.OnServerEvent:Connect(function(player)
	markActivity(player)
end)
