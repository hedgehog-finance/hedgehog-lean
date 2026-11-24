using System.Collections.Concurrent;
using System.Text.Json;
using System.Text;
using QuantConnect.Configuration;
using QuantConnect.Data;
using QuantConnect.Data.Market;
using QuantConnect.Interfaces;
using QuantConnect.Lean.Engine.DataFeeds.Enumerators;
using QuantConnect.Packets;

namespace Gateway.Quant;

// Paper-only bars supplied explicitly through the Gateway feed operation.
// No network, brokerage, account credentials, or synthetic market data.
public sealed class FileDataQueue : IDataQueueHandler
{
    private readonly ConcurrentDictionary<SubscriptionDataConfig, (EnqueueableEnumerator<BaseData> Queue, EventHandler Changed)> subscriptions = new();
    private readonly CancellationTokenSource cancellation = new();
    private readonly Task reader;
    public FileDataQueue() { reader = Task.Run(Read); }
    public bool IsConnected => !cancellation.IsCancellationRequested && !reader.IsFaulted;
    public IEnumerator<BaseData> Subscribe(SubscriptionDataConfig config, EventHandler changed)
    {
        if (config.Type != typeof(TradeBar)) throw new NotSupportedException("Gateway paper feed supports TradeBar subscriptions only");
        var queue = new EnqueueableEnumerator<BaseData>();
        subscriptions[config] = (queue, changed);
        return queue;
    }
    public void Unsubscribe(SubscriptionDataConfig config)
    {
        if (subscriptions.TryRemove(config, out var item)) item.Queue.Dispose();
    }
    public void SetJob(LiveNodePacket job) { }
    private async Task Read()
    {
        using var file = new FileStream(Config.Get("gateway-feed-file"), FileMode.Open, FileAccess.Read, FileShare.ReadWrite);
        using var stream = new StreamReader(file);
        var pending = new StringBuilder();
        var buffer = new char[4096];
        while (!cancellation.IsCancellationRequested)
        {
            // Early feeds remain on disk until the algorithm subscribes. A file
            // append can be observed mid-line, so parse only newline commits.
            if (subscriptions.IsEmpty) { await Task.Delay(100, cancellation.Token); continue; }
            var count = await stream.ReadAsync(buffer.AsMemory(), cancellation.Token);
            if (count == 0) { await Task.Delay(100, cancellation.Token); continue; }
            pending.Append(buffer, 0, count);
            string text = pending.ToString();
            int offset = 0, newline;
            while ((newline = text.IndexOf('\n', offset)) >= 0)
            {
                Publish(text.Substring(offset, newline - offset));
                offset = newline + 1;
            }
            pending.Remove(0, offset);
            if (pending.Length > 1024 * 1024) throw new InvalidDataException("Gateway bar exceeds the feed limit");
        }
    }
    private void Publish(string line)
    {
            using var document = JsonDocument.Parse(line);
            var bar = document.RootElement;
            foreach (var pair in subscriptions)
            {
                var config = pair.Key;
                if (bar.GetProperty("symbol").GetString() != config.Symbol.Value) continue;
                var utc = DateTimeOffset.Parse(bar.GetProperty("time").GetString()!).UtcDateTime;
                var local = NodaTime.Instant.FromDateTimeUtc(utc).InZone(config.ExchangeTimeZone).ToDateTimeUnspecified();
                // Gateway timestamps denote the completed bar. LEAN's Time is
                // the beginning; daily end time is normalized by its calendar.
                var start = config.Resolution == QuantConnect.Resolution.Daily ? local.Date : local - config.Increment;
                var value = new TradeBar(start, config.Symbol, bar.GetProperty("open").GetDecimal(), bar.GetProperty("high").GetDecimal(),
                    bar.GetProperty("low").GetDecimal(), bar.GetProperty("close").GetDecimal(), bar.GetProperty("volume").GetDecimal(), config.Increment);
                pair.Value.Queue.Enqueue(value);
                pair.Value.Changed(this, EventArgs.Empty);
            }
    }
    public void Dispose()
    {
        cancellation.Cancel();
        foreach (var config in subscriptions.Keys) Unsubscribe(config);
        try { reader.GetAwaiter().GetResult(); } catch (OperationCanceledException) { }
        cancellation.Dispose();
    }
}
