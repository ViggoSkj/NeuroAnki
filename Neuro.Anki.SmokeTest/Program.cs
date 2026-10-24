using System;
using System.Collections.Generic;
using System.Net;
using System.Threading.Tasks;
using Neuro.Networking.Anki;

// Intended only for a separately launched --mock service. Creates and deletes its own test note.
string Url = Environment.GetEnvironmentVariable("ANKI_BRIDGE_URL") ?? "http://127.0.0.1:8766/";
string Token = Environment.GetEnvironmentVariable("ANKI_BRIDGE_TOKEN") ?? throw new InvalidOperationException("ANKI_BRIDGE_TOKEN is required.");
using var Http = new System.Net.Http.HttpClient(new System.Net.Http.HttpClientHandler { AllowAutoRedirect = false });
var Health = await Http.GetStringAsync(new Uri(new Uri(Url), "health"));
using var Document = System.Text.Json.JsonDocument.Parse(Health);
if (Document.RootElement.GetProperty("mode").GetString() != "mock") throw new InvalidOperationException("Run this smoke test only against the mock service.");
using IAnkiClient Client = new AnkiClient(Url, Token);
AnkiNote Note = await Client.CreateNoteAsync(new CreateNote
{
    Deck = "Integration::Swedish",
    Fields = new Dictionary<string, string> { ["Front"] = "hej", ["Back"] = "hello" },
    Tags = new List<string> { "integration" }
});
try
{
    if ((await Client.GetNoteAsync(Note.Id)).Fields["Front"] != "hej") throw new Exception("Read failed");
    var Notes = await Client.GetNotesAsync(Deck: "Integration", Tag: "integration");
    if (!Notes.Items.Exists(Item => Item.Id == Note.Id)) throw new Exception("Filter failed");
    var Cards = await Client.GetCardsAsync(NoteId: Note.Id);
    if (Cards.Items.Count != 1) throw new Exception("Card generation failed");
    var Updated = await Client.UpdateNoteAsync(Note.Id, new UpdateNote { Fields = new() { ["Back"] = "hi" }, Tags = new() });
    if (Updated.Fields["Back"] != "hi" || Updated.Tags.Count != 0) throw new Exception("Update failed");
    await Client.GetDecksAsync();
    await Client.GetReviewsAsync();
    if ((await Client.SyncAsync()).Status != "mock_noop") throw new Exception("Unexpected sync result");
    if ((await Client.DownloadCollectionAsync()).Status != "mock_noop") throw new Exception("Unexpected download result");
}
finally { await Client.DeleteNoteAsync(Note.Id); }
try { await Client.GetNoteAsync(Note.Id); throw new Exception("Note still exists after deletion"); }
catch (AnkiApiException Error) when (Error.StatusCode == HttpStatusCode.NotFound) { }
Console.WriteLine("PASS: .NET client -> HTTP mock -> in-memory Anki SQLite database (CRUD, filters, cards, reviews, sync and deletion).");
