using System.Collections.Generic;

namespace Neuro.Networking.Anki
{
    public sealed class Page<T>
    {
        public List<T> Items { get; set; } = new List<T>();
        public long? NextCursor { get; set; }
    }

    public sealed class AnkiDeck
    {
        public long Id { get; set; }
        public string Name { get; set; } = string.Empty;
    }

    public sealed class AnkiNote
    {
        public long Id { get; set; }
        public string Guid { get; set; } = string.Empty;
        public string Model { get; set; } = string.Empty;
        public long ModifiedSeconds { get; set; }
        public Dictionary<string, string> Fields { get; set; } = new Dictionary<string, string>();
        public List<string> Tags { get; set; } = new List<string>();
    }

    public sealed class CreateNote
    {
        public string Model { get; set; } = "Basic";
        public string Deck { get; set; } = "Default";
        public Dictionary<string, string> Fields { get; set; } = new Dictionary<string, string>();
        public List<string> Tags { get; set; } = new List<string>();
    }

    public sealed class UpdateNote
    {
        /// <summary>Only supplied fields are changed; empty strings clear a field.</summary>
        public Dictionary<string, string> Fields { get; set; } = new Dictionary<string, string>();
        /// <summary>Null preserves tags; an empty list clears them.</summary>
        public List<string>? Tags { get; set; }
    }

    public sealed class AnkiCard
    {
        public long Id { get; set; }
        public long NoteId { get; set; }
        public long DeckId { get; set; }
        public string Deck { get; set; } = string.Empty;
        public int Ordinal { get; set; }
        public long ModifiedSeconds { get; set; }
        public int Type { get; set; }
        public int Queue { get; set; }
        /// <summary>Raw Anki due value: its units depend on Type and Queue.</summary>
        public long Due { get; set; }
        public int Interval { get; set; }
        public int Repetitions { get; set; }
        public int Lapses { get; set; }
    }

    public sealed class AnkiReview
    {
        /// <summary>Anki review ID, normally the review's Unix timestamp in milliseconds.</summary>
        public long Id { get; set; }
        public long CardId { get; set; }
        public int Ease { get; set; }
        /// <summary>Anki intervals: positive values are days; negative values are seconds.</summary>
        public int Interval { get; set; }
        public int LastInterval { get; set; }
        public int Factor { get; set; }
        public int DurationMs { get; set; }
        public int Type { get; set; }
    }

    public sealed class SyncResult
    {
        public string Status { get; set; } = string.Empty;
        public string Mode { get; set; } = string.Empty;
    }
}
