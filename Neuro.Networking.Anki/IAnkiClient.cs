using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;

namespace Neuro.Networking.Anki
{
    /// <summary>Identical contract for the real fastanki service and the in-memory mock service.</summary>
    public interface IAnkiClient : IDisposable
    {
        Task<List<AnkiDeck>> GetDecksAsync(CancellationToken CancellationToken = default);
        Task<Page<AnkiNote>> GetNotesAsync(long AfterId = 0, int Limit = 100, string? Deck = null,
            string? Tag = null, CancellationToken CancellationToken = default);
        Task<AnkiNote> GetNoteAsync(long NoteId, CancellationToken CancellationToken = default);
        Task<AnkiNote> CreateNoteAsync(CreateNote Request, CancellationToken CancellationToken = default);
        Task<AnkiNote> UpdateNoteAsync(long NoteId, UpdateNote Request, CancellationToken CancellationToken = default);
        Task DeleteNoteAsync(long NoteId, CancellationToken CancellationToken = default);
        Task<Page<AnkiCard>> GetCardsAsync(long AfterId = 0, int Limit = 100, long? NoteId = null,
            string? Deck = null, CancellationToken CancellationToken = default);
        Task<Page<AnkiReview>> GetReviewsAsync(long AfterId = 0, int Limit = 100, long? CardId = null,
            CancellationToken CancellationToken = default);
        /// <summary>Incremental sync only. A schema mismatch returns a full_sync_required API error.</summary>
        Task<SyncResult> SyncAsync(CancellationToken CancellationToken = default);
        /// <summary>Explicitly replaces the bridge's local collection with AnkiWeb's collection.</summary>
        Task<SyncResult> DownloadCollectionAsync(CancellationToken CancellationToken = default);
    }
}
