using Gateway.Quant;
using QuantConnect;
using QuantConnect.Configuration;
using QuantConnect.Data;
using QuantConnect.Data.Market;

var folder = Path.Combine(Path.GetTempPath(), "quant-feed-" + Guid.NewGuid());
Directory.CreateDirectory(folder);
try
{
    var path = Path.Combine(folder, "feed.jsonl");
    const string bar = "{\"symbol\":\"SPY\",\"time\":\"2024-01-02T14:31:00Z\",\"open\":10,\"high\":11,\"low\":9,\"close\":10,\"volume\":100}";
    await File.WriteAllTextAsync(path, bar + "\n");
    Config.Set("gateway-feed-file", path);
    using var handler = new FileDataQueue();
    // A feed received before subscription must survive engine initialization.
    await Task.Delay(250);
    var symbol = new Symbol(SecurityIdentifier.GenerateEquity("SPY", Market.USA, false), "SPY");
    var config = new SubscriptionDataConfig(typeof(TradeBar), symbol, Resolution.Minute, TimeZones.NewYork, TimeZones.NewYork, false, false, false);
    using var queue = handler.Subscribe(config, (_, _) => { });
    async Task ReadBar()
    {
        for (var attempt = 0; attempt < 40; attempt++)
        {
            if (queue.MoveNext() && queue.Current is TradeBar value)
            {
                if (value.EndTime != new DateTime(2024, 1, 2, 9, 31, 0) || value.Volume != 100) throw new Exception("Incorrect bar semantics");
                return;
            }
            await Task.Delay(50);
        }
        throw new Exception("Feed bar was lost");
    }
    await ReadBar();
    await File.AppendAllTextAsync(path, bar[..30]);
    await Task.Delay(250);
    if (!handler.IsConnected || (queue.MoveNext() && queue.Current is not null)) throw new Exception("Partial line was consumed");
    await File.AppendAllTextAsync(path, bar[30..] + "\n");
    await ReadBar();
    Console.WriteLine("PASS: early feed retained; partial append buffered; minute bar boundary preserved");
}
finally
{
    Directory.Delete(folder, true);
}
