using System.Globalization;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace XRDAnalyzer.Services;

/// Writes UTC timestamps as `yyyy-MM-ddTHH:mm:ssZ` — no fractional seconds —
/// matching Swift's default `ISO8601DateFormatter` (used by `JSONEncoder`'s
/// `.iso8601` strategy in AnalysisStore.swift), so sidecar timestamps
/// round-trip through a Mac-written or Windows-written file either way. Reads
/// any ISO-8601 variant (fractional seconds included) for tolerance.
public sealed class Iso8601DateTimeOffsetConverter : JsonConverter<DateTimeOffset>
{
    private const string Format = "yyyy-MM-ddTHH:mm:ssZ";

    public override DateTimeOffset Read(ref Utf8JsonReader reader, Type typeToConvert, JsonSerializerOptions options) =>
        DateTimeOffset.Parse(reader.GetString()!, CultureInfo.InvariantCulture, DateTimeStyles.AssumeUniversal);

    public override void Write(Utf8JsonWriter writer, DateTimeOffset value, JsonSerializerOptions options) =>
        writer.WriteStringValue(value.UtcDateTime.ToString(Format, CultureInfo.InvariantCulture));
}
