-- StarterPlayerScripts/PlayerActivity.client.lua

local Players = game:GetService("Players")
local UserInputService = game:GetService("UserInputService")
local RunService = game:GetService("RunService")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local player = Players.LocalPlayer
local activityEvent = ReplicatedStorage:WaitForChild("PlayerActivity")

-- Throttle how often we ping the server
local MIN_INTERVAL = 5 -- seconds
local lastSent = 0

local function sendActivityPing()
	local now = os.clock()
	if now - lastSent < MIN_INTERVAL then
		return
	end
	lastSent = now
	activityEvent:FireServer()
end

-- Any keyboard/mouse input counts as activity
UserInputService.InputBegan:Connect(function(input, gameProcessed)
	if gameProcessed then
		return
	end
	sendActivityPing()
end)

-- You can expand this later (movement, camera, etc.)
RunService.RenderStepped:Connect(function()
	-- left empty on purpose – keyboard/mouse is usually enough
end)
